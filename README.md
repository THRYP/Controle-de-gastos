# Controle de Gastos — Planejamento Financeiro Pessoal *(Versão de Teste)*

> **Aviso Acadêmico:** Este projeto é um **trabalho universitário em versão de teste/demonstração**, desenvolvido para o componente curricular **Projeto de Extensão I — Ciência da Computação**. O código é aberto sob a licença MIT para avaliação, testes, forks e melhorias pela comunidade.

Aplicação web desenvolvida em **Python + Flask + SQLite** para controle financeiro pessoal e educação financeira comunitária, integrando gestão de lançamentos, faturas de cartão de crédito, metas financeiras, leitura automática de extratos bancários, login com Google (Gmail) e painel administrativo com isolamento total de dados por usuário.

- **Autor:** Ricardo Alves Peres Junior
- **Licença:** [MIT](LICENSE) — livre para avaliação e modificação por qualquer pessoa.

---

## Principais Funcionalidades

- **Autenticação e Privacidade por Usuário:**
  - Tela de login bloqueando qualquer visualização de tabelas antes da autenticação.
  - **Login com Google (Gmail / OAuth 2.0)** para usuários finais.
  - **Login separado de Administrador** (`/admin/login`) e **Painel Administrativo** (`/admin`) para gerenciar contas (cadastrar, editar, bloquear/ativar e excluir) **sem acesso aos lançamentos ou planejamentos individuais dos usuários**.
  - Isolamento estrito por `user_id` em todas as tabelas e rotas.
- **Resumo Financeiro Interativo (`/`):**
  - Saldo em conta, total guardado em metas, dívida aberta no cartão e patrimônio líquido.
  - Gráficos interativos com `Chart.js` (evolução patrimonial, despesas por categoria e progresso de metas) e tema claro/escuro.
  - Previsão de gastos para o próximo mês com indicador de tendência.
- **Lançamentos (`/lancamentos`):**
  - Cadastro, edição, exclusão e marcação rápida de contas pagas/pendentes.
- **Cartão de Crédito (`/cartao`):**
  - Controle de compras parceladas (1 a 48x), divisão exata em centavos, filtro por banco e marcação de fatura paga por mês/banco.
- **Planejamento e Metas (`/planejamento`):**
  - Criação de metas com prazo, aportes, cálculo automático de aporte mensal necessário e barra de progresso.
- **Leitor de Extratos (`/importar`):**
  - Importação de arquivos `.ofx`, `.qfx`, `.csv`, `.txt` e `.pdf` com detecção automática de banco, categorização automática, filtro de duplicados e opção de desfazer lote.

---

## Tecnologias Utilizadas

- **Backend:** Python 3, Flask, SQLite3, Werkzeug Security
- **Leitura de PDF/Extratos:** `pypdf`, `csv`, `re`
- **Frontend:** Jinja2, HTML5, CSS3 (tema claro/escuro), JavaScript, Chart.js 4
- **Autenticação:** OAuth 2.0 / OpenID Connect (Google)

---

## Como Executar Localmente

1. Clone o repositório e entre na pasta:
   ```bash
   git clone https://github.com/THRYP/Controle-de-gastos.git
   cd Controle-de-gastos
   ```

2. Crie e ative o ambiente virtual:
   ```bash
   python -m venv venv
   # Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # Linux/macOS:
   source venv/bin/activate
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

4. Crie o arquivo `.env` a partir do modelo:
   ```bash
   cp .env.exemplo .env
   ```
   Edite o `.env` e defina:
   - `SECRET_KEY` (gere com `python -c "import secrets; print(secrets.token_hex(32))"`)
   - `ADMIN_EMAIL` e `ADMIN_PASSWORD` (conta inicial do administrador)
   - `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` (obtidos no [Google Cloud Console](https://console.cloud.google.com/apis/credentials) com URI autorizada `http://127.0.0.1:5000/auth/google/callback`)

5. Execute a aplicação:
   ```bash
   python app.py
   ```
   Acesse `http://127.0.0.1:5000/login` (ou `http://127.0.0.1:5000/admin/login` para o painel administrativo).

---

## Como Publicar no PythonAnywhere (Plano Gratuito)

1. Crie uma conta gratuita em [pythonanywhere.com](https://www.pythonanywhere.com/) e abra um console **Bash**.
2. Clone o repositório e crie o ambiente virtual:
   ```bash
   git clone https://github.com/THRYP/Controle-de-gastos.git
   cd Controle-de-gastos
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Crie o arquivo `.env` na pasta do projeto (`cp .env.exemplo .env` e edite com `nano .env`), ajustando:
   - `FLASK_DEBUG=false`
   - `SECRET_KEY` e `ADMIN_PASSWORD` fortes
   - `GOOGLE_REDIRECT_URI=https://SEU_USUARIO.pythonanywhere.com/auth/google/callback`
   - `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`
4. No Google Cloud Console, adicione `https://SEU_USUARIO.pythonanywhere.com/auth/google/callback` em **URIs de redirecionamento autorizados**.
5. Na aba **Web** do PythonAnywhere:
   - Clique em **Add a new web app** → **Manual configuration** → **Python 3.x**.
   - Em **Virtualenv**, informe `/home/SEU_USUARIO/Controle-de-gastos/venv`.
   - Em **Static files**, mapeie URL `/static/` para `/home/SEU_USUARIO/Controle-de-gastos/static`.
   - Edite o arquivo **WSGI configuration file** substituindo seu conteúdo para apontar para o projeto:
     ```python
     import os
     import sys

     path = "/home/SEU_USUARIO/Controle-de-gastos"
     if path not in sys.path:
         sys.path.insert(0, path)

     from wsgi import application
     ```
   - Clique em **Reload** e acesse `https://SEU_USUARIO.pythonanywhere.com`.

---

## Estrutura do Projeto

- `app.py` — Aplicação Flask, regras de negócio, autenticação OAuth/admin e rotas SQLite
- `extrato_parser.py` — Parser de extratos bancários (`.ofx`, `.qfx`, `.csv`, `.txt`, `.pdf`)
- `wsgi.py` — Ponto de entrada WSGI para produção
- `templates/` — Templates HTML (Jinja2)
- `static/` — Folha de estilos (`style.css`), gráficos (`graficos.js`) e imagens
- `.env.exemplo` — Modelo de variáveis de ambiente
