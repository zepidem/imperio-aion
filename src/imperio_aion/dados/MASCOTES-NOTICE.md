# Nomes e retratos das mascotes

O catálogo `mascotes-pt.json` relaciona 207 IDs de mascotes aos nomes em português e retratos originais. Os WebP em `assets/pets/` conservam os bytes do CDN; o aplicativo apenas os reduz em memória para exibição. Não há consultas online durante o jogo.

Fonte dos dados: [AION 2 Global Database — gaming.tools](https://aion2.gaming.tools/pt-br/pets), [JSON público em português](https://cdn-hosted.gaming.tools/aion2/data/pt-br/pets.d.json?version=1791739440423), versão do catálogo `1791739440423`, importada em 11/10/2026. Cada entrada conserva o endereço da página, URL original do retrato, tamanho e SHA-256 do arquivo.

Dados e imagens do jogo: © NCSOFT. O catálogo público é comunitário, mantido por gaming.tools; não há afiliação oficial da ferramenta com o site ou com a NCSOFT. Não se atribui autoria ou licença própria sobre os retratos.

Os IDs são os IDs das mascotes; não são os IDs de NPCs. A referência [Brax 1181](https://aion2.gaming.tools/pt-br/pets/1181) também relaciona a alma `534660200`, e [Escorpião 1067](https://aion2.gaming.tools/pt-br/pets/1067) relaciona a alma `534660068`, correspondendo aos registros confirmados no jogo pelo usuário. O nome exibido para 1067 conserva “Escorpião de Minério”, confirmado pelo usuário na instalação; o nome original do catálogo (“Escorpião”) continua registrado em `catalog_name`.

Todos os 131 IDs nas duas tabelas do diagnóstico de entrada `183902` têm nome e retrato neste catálogo. Isso verifica a cobertura dessa amostra, sem alegar validação ao vivo de cada espécie. Um ID novo sem entrada conserva o nome “Mascote + ID” e o símbolo neutro. Uma imagem ausente ou corrompida também usa o símbolo neutro sem alterar o progresso.
