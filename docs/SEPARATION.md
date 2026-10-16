# Separação do Aion e do BDO

Este repositório foi preparado a partir de uma seleção de arquivos Aion. Não foi clonado nem importado o histórico do repositório privado `zepidem/imperdiobdo`.

O site, backend, banco de dados, credenciais de hospedagem e demais fontes do BDO permanecem no projeto privado existente. Esta preparação não fez commit, push, alteração de visibilidade ou deploy naquele projeto.

O domínio e a página `/aion2` podem continuar no site atual. A futura integração de publicação deve consumir artefatos Aion do novo repositório, mantendo os URLs do canal existentes quando possível. Separar o repositório não altera automaticamente esse fluxo.

Os endereços públicos `imperiobdo.com/aion2` ainda presentes nos fontes identificam o canal existente. Isso não significa que o backend BDO tenha sido incluído. Eles também não autorizam executar os clientes contra produção durante testes.

Não levar para o novo histórico: chaves de assinatura, cofres DPAPI, `.env`, credenciais, dados de jogadores, capturas, dumps, instalações prontas ou arquivos gerados de releases antigos. As âncoras públicas de verificação podem permanecer; os segredos de assinatura não são necessários para ler ou testar os fontes.

O responsável criou o repositório público `https://github.com/zepidem/imperio-aion`, separado do BDO. A publicação usa apenas a árvore preparada do Aion; a migração de releases permanece uma etapa futura. O responsável também autorizou tornar BDO público, mas essa mudança ainda não foi executada nem seu histórico integral revisado.
