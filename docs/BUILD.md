# Fontes e build

## Aplicativo

- Entrada desktop: `src/imperio_aion/desktop_entry.py`.
- Interface e aplicação: `src/imperio_aion/app.py`.
- Leitura e estado: módulos Python no mesmo diretório.
- Referência importada: aplicativo 0.8.71, build revisão 4, 67 arquivos registrados mais entrada conferidos antes da cópia.

Os módulos usam imports locais absolutos. Ao executar uma ferramenta de análise, inclua `src/imperio_aion` em `sys.path`; o runner de testes já faz isso. Não execute a entrada desktop como teste: ela pode observar o jogo e solicitar captura.

## Atualizador e instalador

- Entrada: `src/updater/updater_app.py`.
- Verificadores e fluxo: `update_core.py`, `verify_core.py`, `renewal_core.py`, `update_service.py`.
- Helper nativo: `src/updater/Installer.cs` e `imperio-install.exe.config`.
- A string `__RSA_PUBLIC_XML__` do C# deve ser substituída por uma âncora RSA pública no build; nunca por material privado.
- `tools/build_native.py` compila uma cópia do helper em `build/native`. Ele não o executa, instala ou eleva, não modifica a fonte e não atualiza automaticamente a confiança do atualizador.

```powershell
python tools/build_native.py
```

## Por que o instalador completo ainda não está preparado

1. As imagens e os ícones existentes foram recuperados a pedido do responsável, com autorização pendente. Catálogos e alguns recursos auxiliares ainda estão excluídos, incluindo o HTML da sobreposição. É necessário resolver direitos, completar os recursos de runtime e testar os estados sem catálogo.
2. O perfil importado referenciava nomes de uma tabela comunitária. Nesta árvore a configuração de nomes está vazia, para não redistribuir essa tabela sem revisão. Isso afeta a identificação de habilidades e precisa de validação funcional.
3. Os scripts anteriores de empacotamento dependiam de diretórios e ambientes locais. A configuração portátil de PyInstaller deve ser fechada depois do inventário de recursos e wheels; não foi copiado um script que apontasse silenciosamente às pastas privadas antigas.
4. É necessário definir wheels e hashes, os avisos das bibliotecas nativas embarcadas e a fonte correspondente. `requirements-observed.txt` é apenas um inventário.
5. Recompilar o helper muda seu hash. A confiança do atualizador, a assinatura dos metadados e a publicação de uma versão precisam ser feitas em conjunto; os arquivos atuais de confiança são referências públicas, sem chaves privadas.
6. Compilar e verificar em Windows limpo; então relacionar commit, fontes e artefatos do release.

`tools/check_release.py` retorna as pendências. Os testes offline validam partes do código separado, não certificam uma distribuição completa ou uma aprovação da SignPath.

## CI de verificação preparado

`.github/workflows/verify.yml` usa um runner Windows hospedado, Python 3.14.7 x64 e as dependências diretas de `requirements.txt`. As actions checkout 7.0.1 e setup-python 7.0.0 estão fixadas pelos commits publicados nos repositórios oficiais. O workflow roda auditoria, testes offline e compilação do helper, sem iniciar o aplicativo, a captura ou o instalador. A instalação inicial das dependências exige acesso ao índice de pacotes; somente a suíte de testes roda com conexões bloqueadas pelo runner de testes.

O workflow não contém `pull_request_target`, credenciais persistidas, acesso a segredos de assinatura, deploy ou upload de releases. A primeira execução hospedada permanece pendente até publicar o repositório. A instalação por versão ainda não é um lock de todos os wheels por hash: esse lock pertence à etapa de build completo.

Referências das actions: https://github.com/actions/checkout/releases/tag/v7.0.1 e https://github.com/actions/setup-python/releases/tag/v7.0.0.
