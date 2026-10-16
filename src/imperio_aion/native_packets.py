# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, passive IP/TCP adapter for complete native Windows packet events.

PacketType 1 is Ethernet and PacketType 3 starts at an IP header. There is no
signature search or reframing. ``None`` means a valid envelope without TCP;
``PacketParseError`` means the caller must mark capture incomplete. IPv4/IPv6
fragments (including IPv6 atomic Fragment headers), jumbograms, encrypted ESP
and unsupported extension headers are rejected rather than partially decoded.

TCP reassembly remains in LiveReader. Packet/stream identifiers, ownership and
connection generations come from its controller. Checksums are not validated:
native events may precede checksum offload. Ethernet padding/FCS is excluded
using the declared IP length; raw-IP events must have exactly that length.

Wire layout references: RFC 791, RFC 8200, RFC 4302 and RFC 9293.
"""
from decimal import Decimal, InvalidOperation
import ipaddress

ETHERNET = 1
RAW_IP = 3
MAX_PACKET_BYTES = 65_575 + 256
MAX_VLAN_TAGS = 4
MAX_IPV6_EXTENSIONS = 16
MAX_IPV6_EXTENSION_BYTES = 4096


class PacketParseError(ValueError):
    """Malformed or truncated packet: do not treat the capture as complete."""


class UnsupportedPacketError(PacketParseError):
    """A packet shape requires support not provided by this bounded adapter."""


class FragmentedPacketError(UnsupportedPacketError):
    """IP fragmentation is not reassembled by this initial native adapter."""


def _need(data, start, size, label):
    if start < 0 or size < 0 or start + size > len(data):
        raise PacketParseError(label + ' truncado.')


def _options(data, label, tcp=False):
    """Check TLV boundaries without changing the network header's semantics."""
    at = 0
    while at < len(data):
        kind = data[at]
        if kind == 0:
            if any(data[at + 1:]):
                raise PacketParseError('Preenchimento de ' + label + ' inválido.')
            return
        if kind == 1:
            at += 1
            continue
        _need(data, at, 2, 'Opção de ' + label)
        size = data[at + 1]
        if size < 2:
            raise PacketParseError('Comprimento de opção de ' + label + ' inválido.')
        _need(data, at, size, 'Opção de ' + label)
        if tcp:
            fixed = {2: 4, 3: 3, 4: 2, 8: 10}
            if kind in fixed and size != fixed[kind]:
                raise PacketParseError('Comprimento de opção TCP conhecida inválido.')
            if kind == 5 and (size < 10 or (size - 2) % 8):
                raise PacketParseError('Blocos SACK fora do formato TCP.')
        at += size


def _ipv6_options(data):
    at = 2
    while at < len(data):
        kind = data[at]
        if kind == 0:  # Pad1 has no length byte.
            at += 1
            continue
        _need(data, at, 2, 'Opção IPv6')
        size = 2 + data[at + 1]
        _need(data, at, size, 'Opção IPv6')
        if kind == 0xC2:
            raise UnsupportedPacketError('Jumbogramas IPv6 ainda não são suportados.')
        if kind == 1 and any(data[at + 2:at + size]):
            raise PacketParseError('Preenchimento PadN IPv6 inválido.')
        at += size


def _tcp(segment, version, source, destination):
    _need(segment, 0, 20, 'Cabeçalho TCP')
    source_port = int.from_bytes(segment[0:2], 'big')
    destination_port = int.from_bytes(segment[2:4], 'big')
    if not source_port or not destination_port:
        raise PacketParseError('Porta TCP reservada fora do perfil.')
    header_size = (segment[12] >> 4) * 4
    if header_size < 20:
        raise PacketParseError('Tamanho de cabeçalho TCP inválido.')
    _need(segment, 0, header_size, 'Cabeçalho/opções TCP')
    if segment[12] & 15:
        raise UnsupportedPacketError('Bits reservados do cabeçalho TCP não suportados.')
    _options(segment[20:header_size], 'TCP', tcp=True)
    flags = segment[13]
    return dict(ip_version=version, source=source, destination=destination,
                source_port=source_port, destination_port=destination_port,
                tcp_seq_raw=int.from_bytes(segment[4:8], 'big'),
                tcp_ack_raw=int.from_bytes(segment[8:12], 'big'), tcp_flags=flags,
                syn=bool(flags & 2), fin=bool(flags & 1), reset=bool(flags & 4),
                payload=bytes(segment[header_size:]))


def _ipv4(data, ethernet):
    _need(data, 0, 20, 'Cabeçalho IPv4')
    header_size = (data[0] & 15) * 4
    if header_size < 20:
        raise PacketParseError('IHL IPv4 inválido.')
    total_size = int.from_bytes(data[2:4], 'big')
    if total_size < header_size:
        raise PacketParseError('Comprimento total IPv4 menor que o cabeçalho.')
    _need(data, 0, total_size, 'Datagrama IPv4')
    if not ethernet and total_size != len(data):
        raise PacketParseError('Bytes extras no evento IP IPv4.')
    data = data[:total_size]
    fragment = int.from_bytes(data[6:8], 'big')
    if fragment & 0x8000:
        raise PacketParseError('Flag reservada IPv4 inválida.')
    if fragment & 0x3FFF:
        raise FragmentedPacketError('Fragmentação IPv4 não é suportada; captura parcial.')
    _options(data[20:header_size], 'IPv4')
    if data[9] != 6:
        return None
    return _tcp(data[header_size:], 4,
                str(ipaddress.IPv4Address(data[12:16])),
                str(ipaddress.IPv4Address(data[16:20])))


def _ipv6(data, ethernet):
    _need(data, 0, 40, 'Cabeçalho IPv6')
    payload_size = int.from_bytes(data[4:6], 'big')
    next_header = data[6]
    if not payload_size and next_header == 0:
        raise UnsupportedPacketError('IPv6 com possível jumbograma não suportado.')
    total_size = 40 + payload_size
    _need(data, 0, total_size, 'Datagrama IPv6')
    if not ethernet and total_size != len(data):
        raise PacketParseError('Bytes extras no evento IP IPv6.')
    data = data[:total_size]
    at = 40
    extensions = 0
    while next_header in (0, 43, 44, 50, 51, 60, 135, 139, 140, 253, 254):
        if extensions >= MAX_IPV6_EXTENSIONS or at - 40 > MAX_IPV6_EXTENSION_BYTES:
            raise UnsupportedPacketError('Limite de extensões IPv6 atingido.')
        extensions += 1
        if next_header == 44:
            _need(data, at, 8, 'Cabeçalho Fragment IPv6')
            raise FragmentedPacketError('Cabeçalho Fragment IPv6 não suportado; captura parcial.')
        if next_header in (50, 135, 139, 140, 253, 254):
            raise UnsupportedPacketError('Extensão IPv6 ainda não suportada: ' + str(next_header))
        if next_header == 0 and at != 40:
            raise PacketParseError('Hop-by-Hop IPv6 fora da primeira posição.')
        _need(data, at, 2, 'Cabeçalho de extensão IPv6')
        size = (data[at + 1] + 2) * 4 if next_header == 51 else (data[at + 1] + 1) * 8
        if next_header == 51 and (size < 16 or size % 8):
            raise PacketParseError('Comprimento AH IPv6 inválido.')
        _need(data, at, size, 'Cabeçalho de extensão IPv6')
        if at + size - 40 > MAX_IPV6_EXTENSION_BYTES:
            raise UnsupportedPacketError('Limite de bytes de extensões IPv6 atingido.')
        if next_header in (0, 60):
            _ipv6_options(data[at:at + size])
        if next_header == 51 and data[at + 2:at + 4] != b'\0\0':
            raise PacketParseError('Campo reservado AH IPv6 inválido.')
        next_header = data[at]
        at += size
    if next_header != 6:
        return None
    return _tcp(data[at:], 6, str(ipaddress.IPv6Address(data[8:24])),
                str(ipaddress.IPv6Address(data[24:40])))


def parse_packet(data, packet_type):
    """Return a TCP segment dictionary, None for non-TCP, or an explicit error.

    No ownership, filtering, checksum validation or TCP-stream identity is
    inferred here. Unknown native packet types fail instead of guessing offsets.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise PacketParseError('O evento nativo não contém bytes de pacote.')
    data = bytes(data)
    if not data or len(data) > MAX_PACKET_BYTES:
        raise PacketParseError('Comprimento do evento nativo fora do limite.')
    if type(packet_type) is not int or packet_type not in (ETHERNET, RAW_IP):
        raise UnsupportedPacketError('PacketType nativo ainda não suportado.')
    ethernet = packet_type == ETHERNET
    expected_version = None
    if ethernet:
        _need(data, 0, 14, 'Cabeçalho Ethernet')
        ethertype = int.from_bytes(data[12:14], 'big')
        at = 14
        tags = 0
        while ethertype in (0x8100, 0x88A8, 0x9100):
            if tags >= MAX_VLAN_TAGS:
                raise UnsupportedPacketError('Limite de tags VLAN atingido.')
            _need(data, at, 4, 'Tag VLAN')
            ethertype = int.from_bytes(data[at + 2:at + 4], 'big')
            at += 4
            tags += 1
        if ethertype <= 1500:
            raise UnsupportedPacketError('Envelope IEEE 802.3/LLC não suportado.')
        if ethertype not in (0x0800, 0x86DD):
            return None
        expected_version = 4 if ethertype == 0x0800 else 6
        data = data[at:]
    _need(data, 0, 1, 'Cabeçalho IP')
    version = data[0] >> 4
    if expected_version is not None and version != expected_version:
        raise PacketParseError('Versão IP difere do EtherType.')
    if version == 4:
        return _ipv4(data, ethernet)
    if version == 6:
        return _ipv6(data, ethernet)
    raise UnsupportedPacketError('Versão IP não suportada no evento nativo.')


def packet_row(packet, *, timestamp, packet_id, stream_id):
    """Convert parse_packet output into LiveReader's existing FIELDS contract.

    The controller must supply a capture timestamp and a unique connection epoch
    for stream_id. This helper never assigns sequences or merges connections.
    """
    if packet is None:
        raise PacketParseError('Um pacote sem TCP não produz linha de leitura.')
    if type(packet_id) is not int or packet_id < 0:
        raise PacketParseError('Identificador de pacote inválido.')
    if type(stream_id) is int:
        valid_stream = stream_id >= 0
    else:
        valid_stream = isinstance(stream_id, str) and 0 < len(stream_id) <= 20 and stream_id.isascii() and stream_id.isdecimal()
    if not valid_stream:
        raise PacketParseError('Identificador de conexão inválido.')
    epoch = str(timestamp)
    try:
        number = Decimal(epoch)
        valid_time = len(epoch) <= 50 and number.is_finite() and 0 <= number < Decimal('1e12')
    except (InvalidOperation, ValueError):
        valid_time = False
    if not valid_time:
        raise PacketParseError('Timestamp de captura inválido.')
    version = packet['ip_version']
    if version not in (4, 6):
        raise PacketParseError('Versão do segmento inválida.')
    row = dict.fromkeys(('frame.number', 'frame.time_epoch', 'tcp.stream', 'ip.src',
        'ip.dst', 'ipv6.src', 'ipv6.dst', 'tcp.srcport', 'tcp.dstport', 'tcp.seq_raw',
        'tcp.len', 'tcp.payload', 'tcp.flags.syn', 'tcp.flags.fin', 'tcp.flags.reset'), '')
    family = 'ip' if version == 4 else 'ipv6'
    row.update({'frame.number': str(packet_id), 'frame.time_epoch': epoch,
        'tcp.stream': str(stream_id), family + '.src': packet['source'],
        family + '.dst': packet['destination'], 'tcp.srcport': str(packet['source_port']),
        'tcp.dstport': str(packet['destination_port']), 'tcp.seq_raw': str(packet['tcp_seq_raw']),
        'tcp.len': str(len(packet['payload'])), 'tcp.payload': packet['payload'].hex(),
        'tcp.flags.syn': str(int(packet['syn'])), 'tcp.flags.fin': str(int(packet['fin'])),
        'tcp.flags.reset': str(int(packet['reset']))})
    return row
