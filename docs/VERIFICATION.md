# Verificação desta preparação

Data: 16/10/2026.

- 687 arquivos examinados após a inclusão solicitada dos recursos visuais; nenhum achado nas regras e padrões examinados. Os 534 recursos possuem hashes conferidos e exceções explícitas no auditor para imagens inventariadas; não houve liberação jurídica dessas imagens.
- 88 arquivos Python passaram na análise sintática.
- 91 arquivos de fontes/avisos de runtime têm origem e hashes registrados, incluindo o perfil de dados substituído por configuração vazia.
- 69 testes offline passaram: limites do protocolo com amostras sintéticas, comportamento sem catálogos, verificação e extração de atualizações, preservação de arquivos, preferências e fluxo de abertura. A rede ficou bloqueada durante o runner e os diretórios de usuário foram isolados.
- O helper C# foi compilado pelo compilador .NET Framework do Windows, usando apenas âncoras públicas. SHA-256 do executável local de ensaio: `bfb995b137c7c6d1904d9f295ccb98016ba926cd88d026485e0d6d02929890d3`.
- O helper não foi executado, instalado, assinado nem incorporado a uma distribuição. O hash de confiança do atualizador não foi alterado.
- O diretório de build e os arquivos temporários são ignorados pelo Git e não integram o arquivo de fontes gerado por `git archive`.
- A suíte de 69 testes passou novamente após a inclusão das imagens. Os avisos de preparação nos dois JSON legais foram atualizados e seus hashes registrados; o comportamento dos módulos de runtime não foi alterado nesta etapa.
- O workflow Windows em `.github/workflows/verify.yml` usa actions fixadas por commit, permissões de leitura e não assina/publica binários. Após corrigir as aspas no comando de instalação, a [execução 38966883240](https://github.com/zepidem/imperio-aion/actions/runs/38966883240) passou no commit `8a4c906bb760368b2b66627d1b236c9573bf6453`: auditoria dos 687 arquivos, 69 testes e compilação do helper nativo. O helper não foi executado ou instalado.
- Fontes e imagens foram publicados no commit `85a682ed706497e91b035fd415f49202a0f79bda`. A árvore Git `3e6bf5432461cc2a02845034ebaa95b661a901e3` corresponde exatamente à árvore local conferida (687 arquivos). O histórico local anterior foi preservado na branch local `local-prepublication-history`.

Não foi realizado build completo da HUD/atualizador, teste visual, instalação ou captura de rede. Não foi demonstrada equivalência funcional desta árvore incompleta ao executável em uso. A exclusão das tabelas pode reduzir a identificação de habilidades/mascotes; outros recursos auxiliares ausentes podem impedir telas de abrir.

A varredura de padrões não demonstra ausência absoluta de segredos ou vulnerabilidades. A revisão completa dos binários transitivos, dos recursos e da candidatura SignPath permanece descrita em LICENSE_REVIEW.md e BUILD.md.
