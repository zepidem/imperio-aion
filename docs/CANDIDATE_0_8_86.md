# Candidato local 0.8.86

Este candidato continua fora de produção e precisa de teste no jogo.

O leitor aproveita campos explícitos de nome e identificador em perfis SA
observados de efeitos de combate. Apenas personagens já confirmados por
atributos próprios ou pelas mensagens exclusivas do grupo recebem esses nomes.
O identificador da entidade criada nunca é confundido com o do jogador.

Os perfis aceitos distinguem o conjurador de Aura Divina / Armadilha de
Explosão e o alvo da Petrificação de Thamon. Outros modelos, efeitos 1c,
variantes desconhecidas, campos truncados e conexões sem propriedade confirmada
continuam sem atribuição. Não se associam nomes por classe, posição ou dano.

O nome de combate pode aparecer sem confirmar login, servidor, saldos ou mascotes.
Uma identidade completa exige também um servidor explícito ou uma associação
única à lista de grupo atual. Nomes conflitantes continuam pendentes.

No replay privado comparativo, um integrante foi identificado em 12,34 s em vez
de 167,44 s; o personagem local, em 75,19 s em vez de 99,69 s. Outra captura
passou a identificar o personagem local em 38,20 s, sem um cabeçalho próprio
durante o trecho. Os eventos e valores de dano permaneceram idênticos.

Isso não garante nomes instantâneos para todas as classes ou situações. Na
gravação solo mais recente não havia esses efeitos com nome: este ajuste não
antecipa a identificação nesse caso. As gravações não acompanham estes fontes.

Pesquisa de protocolo: https://github.com/taengu/A2Tools-DPS-Meter/blob/0fdd7d2/src-tauri/src/capture/stream_processor.rs .
Implementação independente, validada contra os campos estruturais das capturas SA.
Não foram adicionados pedidos ao servidor, manipulação do jogo ou leitura de memória.
Os diagnósticos e o atalho de gravação inicial continuam locais e opcionais.
