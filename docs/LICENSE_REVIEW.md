# Revisão de licenças — 16/10/2026

Esta revisão cobre fontes, dependências fixadas e os componentes nativos do candidato Windows. O mapeamento dos binários, das licenças embarcadas e das opções escolhidas está em `NATIVE_LICENSES.md`. O builder inclui avisos no instalador e no aplicativo, além da fonte do WinDivert. Os direitos das imagens/dados continuam pendentes; nenhum binário de dependência é armazenado nesta árvore de fontes.

| Componente | Licença verificada | Resultado e obrigação |
| --- | --- | --- |
| Código próprio Aion e atualizador | GPL-3.0-only | Escolha autorizada pelo responsável; preservar licença e disponibilizar fonte correspondente ao distribuir binários |
| Pillow 12.3.0 | MIT-CMU | Licença permissiva; preservar avisos. Avisos completos das bibliotecas de imagem do wheel preservados; opção FTL do FreeType selecionada |
| cryptography 50.0.1 | Apache-2.0 OR BSD-3-Clause | Alternativas permissivas; preservar avisos. OpenSSL 4.0.2 e SBOM upstream Rust incluídos; licenças das 32 crates externas recuperadas de arquivos verificados por hash |
| cffi 2.1.1 | MIT-0 | Texto local preservado; avisos libffi preservados no runtime Python |
| pycparser 3.11 (build atual; 3.0 no inventário inicial) | BSD-3-Clause | Avisos preservados |
| PyInstaller 6.22.3 | GPL-2.0-or-later com exceção; alguns arquivos Apache-2.0 | A exceção permite distribuir o executável sob a licença do aplicativo; conferir os componentes efetivamente embarcados |
| pyinstaller-hooks-contrib 2026.8 | Hooks comuns GPL-2.0-or-later; hooks de runtime Apache-2.0 | Distinção confirmada no texto local, preservado integralmente |
| altgraph 0.17.5, pefile 2024.8.26, setuptools 84.0.0 | MIT | Avisos preservados, incluindo avisos vendorizados encontrados em setuptools |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause | Alternativas permissivas; textos preservados |
| pywin32-ctypes 0.2.3 | BSD-3-Clause | Avisos preservados |
| CPython 3.14.7 | PSF e avisos de componentes | LICENSE.txt local preservado; Tcl/Tk, OpenSSL 3.5.7 e runtime Microsoft relacionados em NATIVE_LICENSES.md |
| WinDivert 2.2.2 | LGPL versão 3, opção selecionada entre as alternativas upstream | Preservar licença, avisos e fonte correspondente; respeitar substituição/relinkagem aplicável. Não reassinar driver upstream como código próprio |

As licenças principais identificadas admitem uso na composição GPLv3 descrita, desde que se cumpram seus termos. Isso não resolve os direitos dos recursos visuais ou equivale a autorização dos titulares ou a aprovação da SignPath. `metadata/dependencies.json` registra versões, escopo e hashes dos textos copiados.

## Revisão inicial dos recursos (antes da inclusão solicitada)

Atualização: o responsável posteriormente pediu a inclusão das imagens mesmo sem resposta da NCSOFT. As imagens foram recuperadas, preservando hashes e direitos pendentes em `metadata/included-resources.json` e `docs/RESOURCE_RIGHTS.md`. As decisões de exclusão abaixo registram a revisão inicial; continuam registradas como revisão histórica para recursos ainda listados em `metadata/excluded-resources.json`. Isso não constitui liberação de direitos.

- Retratos de mascotes e ícones de habilidades: origem no jogo/CDN NCSOFT confirmada pelo responsável e pelos avisos locais. Não foi demonstrada licença que permita esta redistribuição. Excluídos.
- Catálogo de mascotes gaming.tools: a página reserva os direitos dos recursos do AION 2 à NCSOFT; não foi encontrada nela uma concessão open source. Excluído.
- Tabelas NPCs/habilidades: os avisos locais atribuem GPL-3.0 ao projeto comunitário e citam um commit. A referência upstream não pôde ser recuperada nesta consulta. A licença declarada pelo redistribuidor também não prova, sozinha, a titularidade dos recursos do jogo. Tabelas restauradas para o candidato, com os avisos originais e pendência de direitos explicitamente mantida.
- Ícones de classe criados pelo responsável, inspirados no jogo: não considerados automaticamente irregulares. A originalidade e os elementos reproduzidos precisam de análise individual; ficaram pendentes junto ao restante do kit.
- Fontes, logos e demais imagens do kit: sem prova individual de origem/licença suficiente nesta revisão. Excluídos.
- Capturas de rede, sessões de jogadores, diagnósticos e fixtures reais: fora do escopo público e dos testes copiados.

O inventário é `metadata/excluded-resources.json`. Os arquivos originais continuam preservados em suas pastas de trabalho.

## Crédito, autorização e imagens novas

Citar a fonte e reconhecer os direitos da NCSOFT não concede autorização para reprodução. Uma política de conteúdo de fãs pode permitir certos usos, mas é necessário verificar produto, território, finalidade e condições. As referências de políticas encontradas para AION não demonstraram autorização aplicável às imagens de AION 2 neste aplicativo.

Ideias como fogo, cura ou escudo podem orientar uma arte original. Traçar novamente o desenho específico, mantendo sua expressão artística, pode continuar sendo uma adaptação. Não existe percentual universal de mudança que resolva isso. Para recursos novos, registrar autoria, origem e licença por arquivo. Uma arte original do responsável pode receber licença aberta; não aplicar essa licença automaticamente aos recursos atuais.

## Referências consultadas

Atualização de 16/10/2026: foi localizado o anúncio oficial do kit de fãs de AION 2, com logotipo, símbolos de classe, emblemas e wallpapers. O anúncio exige observar as diretrizes de fãs, mas não basta para demonstrar autorização dos ícones de habilidades/mascotes ou sua distribuição com um aplicativo e seus forks. O texto integral das diretrizes não pôde ser recuperado pelo acesso disponível. O responsável informou que enviou um pedido à NCSOFT; a resposta continua pendente. Posteriormente, as imagens foram incluídas a pedido do responsável, com direitos ainda pendentes; os catálogos também foram restaurados para o build completo, sem considerar os direitos resolvidos.

- Anúncio oficial do kit AION 2: https://about.ncsoft.com/jp/news/article/AION2_update_260818
- Diretrizes indicadas pelo anúncio: https://aion2.plaync.com/ja-jp/board/rules/view?articleId=6a7d9723bcc86319892231b1

- GNU GPLv3: https://opensource.org/license/gpl-3.0
- PyInstaller: https://pyinstaller.org/en/stable/license.html
- Pillow: https://pillow.readthedocs.io/en/stable/about.html
- CPython: https://docs.python.org/3/license.html
- WinDivert 2.2.2: https://github.com/basil00/WinDivert/blob/v2.2.2/LICENSE
- gaming.tools: https://aion2.gaming.tools/pt-br/pets
- Lei 9.610/1998, especialmente arts. 7º, 8º e 29: https://www.planalto.gov.br/ccivil_03/leis/l9610.htm
- SignPath: https://signpath.org/terms.html

A elegibilidade SignPath exige uma avaliação adicional do projeto e da distribuição; não está demonstrada apenas pela licença do código.
