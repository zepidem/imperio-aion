# Império Aion

Código-fonte do monitor Windows Império Aion e de seu instalador/atualizador, separado do site e backend do Império BDO.

Repositório: https://github.com/zepidem/imperio-aion. Página do aplicativo: https://imperiobdo.com/aion2.

**Estado: build de candidato Windows completo; direitos e publicação pendentes.** O build inclui aplicativo, instalador/atualizador, catálogos, overlay e dependências. Os recursos de terceiros foram incluídos a pedido do responsável, enquanto se aguarda a NCSOFT; não recebem a GPL do código. O candidato ainda precisa de autorização de release. Não há aprovação nem assinatura SignPath. Consulte [os direitos dos recursos](docs/RESOURCE_RIGHTS.md) e [as instruções de build](docs/BUILD.md).

## Licença

O código próprio é disponibilizado sob **GNU GPL versão 3, exclusivamente — `GPL-3.0-only`**, conforme a escolha do responsável em 16/10/2026. Consulte [LICENSE](LICENSE) e [COPYRIGHT.md](COPYRIGHT.md). Dependências e seus avisos conservam suas licenças. Esta licença não abrange marcas, imagens ou dados de terceiros.

## Conteúdo

| Pasta | Conteúdo |
| --- | --- |
| `src/imperio_aion` | Fontes do aplicativo 0.8.71, referência de build revisão 4 |
| `src/updater` | Atualizador 0.4.2 e instalador nativo `Installer.cs` |
| `tests` | Testes offline com dados sintéticos e verificações da separação |
| `licenses/third-party` | Textos de licença encontrados nas dependências examinadas |
| `metadata` | Origem e hashes dos fontes, dependências, imagens incluídas e recursos ainda excluídos |
| `docs` | Revisão de licenças, recursos e plano de publicação |

A numeração identifica as referências importadas, não um novo lançamento. A versão 0.8.72 encontrada em desenvolvimento não foi usada para não confundir uma árvore em alteração com o build conferido.

## Verificação

Python 3.14 foi usado na conferência local. Instale as dependências em um ambiente virtual próprio antes dos testes, se necessário:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools/audit_repository.py
.venv\Scripts\python tools/run_tests.py
```

`requirements-windows.lock` fixa por hash os wheels do build Windows com Python 3.14.7 x64. `requirements-observed.txt` preserva o inventário anterior. Os 69 testes offline não abrem a HUD, não iniciam captura, não instalam o driver, não modificam o registro e não usam o canal online. A verificação adicional do candidato renderiza janelas ocultas e testa instalação/remoção em pasta isolada. Os testes antigos baseados em capturas de jogadores não foram copiados.

## Build e publicação

```powershell
python tools/check_release.py
```

O comando informa as pendências atuais e retorna código 2 enquanto o release não está autorizado. Não substitua o instalador do site apenas por ter compilado o candidato. [BUILD.md](docs/BUILD.md) descreve build, testes e a ordem das assinaturas.

O site `https://imperiobdo.com/aion2` pode permanecer no repositório privado existente. O novo projeto será responsável pelos fontes e builds do Aion; a integração futura entregará os artefatos ao canal do site. Não foram copiados backend, dados, credenciais ou histórico Git do BDO. Consulte [SEPARATION.md](docs/SEPARATION.md).

## Code signing policy

O projeto ainda não está aprovado pela SignPath Foundation. Não atribuímos à SignPath assinaturas que ela não realizou. Os builds atuais usam verificações próprias de integridade, distintas de Authenticode. Antes da candidatura, precisamos fechar a composição licenciada e a correspondência verificável entre fonte e binário, além dos requisitos do programa.

Consulte a [Code signing policy](docs/CODE_SIGNING_POLICY.md), a [política de privacidade dos fontes](docs/PRIVACY.md) e a [lista de preparação para publicação](docs/RELEASE_CHECKLIST.md). As políticas descrevem o estado atual e os controles ainda a implementar; não demonstram aprovação do programa.

## Contribuição e verificação automática

[CONTRIBUTING.md](CONTRIBUTING.md) explica como propor alterações; [SECURITY.md](SECURITY.md) informa o canal para relatos privados. O responsável inicial de revisão é `zepidem`.

O workflow `.github/workflows/verify.yml` verifica os fontes no Windows, executa os testes offline, compila o candidato completo e confere todos os arquivos do pacote; abertura e instalação isolada são verificadas separadamente, sem elevação. Acompanhe as execuções em [GitHub Actions](https://github.com/zepidem/imperio-aion/actions). Os artefatos aprovados nos testes ficam disponíveis por 14 dias. Ele não publica um release, não recebe credenciais de assinatura e usa apenas permissão de leitura do repositório.

Comunidade informada pelo responsável: [Discord do Império Aion](https://discord.gg/2cQSFwVst).

Página do programa: https://signpath.org/terms.html
