# SIRI — Robô de publicação automática no Instagram

Este robô roda todo dia (via GitHub Actions) e lê os posts agendados
**direto do calendário do seu app de orçamento** ("📅 Posts do Mês")
— não usa mais planilha separada nenhuma. Você programa tudo por lá,
do jeito que já está acostumado, e o robô só executa.

## Como funciona, na prática

1. No seu app, abre **"📅 Posts do Mês"**, clica no dia, adiciona um
   post.
2. Preenche a **Plataforma**: marca a caixinha de `Instagram` (pode
   marcar outras junto, como Facebook — o Facebook já publica
   automaticamente junto com o Instagram, então não precisa fazer
   nada a mais por ele). TikTok e YouTube ainda não são publicados
   por este robô — marcar essas caixinhas hoje não tem efeito, é só
   preparação pro futuro. Escreve a **Legenda**, e escolhe o
   **Status**: deixe em `Rascunho` enquanto ainda está decidindo, e
   só muda pra `Aprovado` quando quiser que o robô publique de
   verdade — ele **ignora tudo que estiver em Rascunho**.
3. No bloco roxo novo do cartão de post, escolhe o **"Tipo p/
   Post_BOT"**:
   - **Carrossel automático**: fotos aleatórias da Biblioteca — não
     precisa preencher o campo Arquivo.
   - **Story automático**: idem, mas vira story. Pode deixar o campo
     Arquivo vazio (aleatório) ou indicar o nome exato de uma foto
     específica.
   - **Vídeo programado**: preenche o campo Arquivo com o nome exato
     do vídeo (ex: `entrega_cliente_x.mp4`), que precisa estar
     dentro da subpasta **"Biblioteca/Posts Programados"** no Drive.
   - **Carrossel de projeto**: preenche o campo Arquivo com o nome
     da **subpasta** com a curadoria daquele trabalho (ex:
     `CocaCola`), dentro de "Posts Programados" — o robô publica
     tudo que tiver lá dentro (fotos e/ou vídeo), na ordem em que
     estiver no Drive.
4. No dia marcado, o robô publica sozinho e marca o post como
   `Publicado` de volta no seu app — você vê isso normalmente no
   calendário, sem precisar abrir nada a mais.

## Por que não usa mais Planilha Google

Testamos ler de uma Planilha separada, mas seu app **já tinha** um
calendário visual completo — só faltavam 2 campos. Ler direto do
mesmo banco de dados (o `siri_database.json` que seu app já sincroniza
com o GitHub) elimina duplicidade e mantém uma fonte única de verdade.

**Sobre segurança dessa leitura compartilhada:** o robô só **lê**
esse arquivo, e a única coisa que ele escreve de volta é o campo
`status` do post que acabou de publicar — sempre buscando a versão
mais recente do arquivo bem na hora de gravar (nunca uma cópia
antiga), para não conflitar com o autosave do seu app. Se por acaso
os dois tentarem salvar ao mesmo tempo, o robô detecta e tenta de
novo automaticamente.

## Passo 1 — Separar o repositório de dados do repositório de código

**Importante, resolve dois problemas de uma vez:** hoje seu app salva
dados (`siri_database.json`) no mesmo repositório que a Vercel usa
pra publicar o site. Cada commit de dado pode disparar um novo deploy
— e o autosave salva a cada 30 segundos. Isso:
(a) gasta a cota de deploys da Vercel à toa, e
(b) deixa qualquer escrita do robô "correndo" contra o autosave do
app no mesmo repositório.

**Recomendado:** cria um **segundo repositório**, privado, só pros
dados (ex: `siri-dados`), e move o `siri_database.json` pra lá. No
seu app, na tela **"⚙️ Conexão GitHub"**, troca o "Nome do
Repositório" pra esse novo. A Vercel nunca fica sabendo desse
repositório (não conecta ele a nada), então nenhuma escrita de dado
dispara deploy nunca mais.

Se preferir não separar agora, o robô funciona do mesmo jeito — só
que os dois problemas acima continuam.

## Passo 2 — Subir os arquivos deste pacote

Cria um repositório novo (pode ser esse mesmo `siri-instagram-bot`)
e sobe todos os arquivos deste pacote.

## Passo 3 — Gerar um token do GitHub para o robô

O robô precisa de um **Personal Access Token (PAT)** do GitHub com
permissão de leitura E escrita no repositório de dados (Passo 1):

1. No GitHub, vai em **Settings** (da sua conta) → **Developer
   settings** → **Personal access tokens** → **Fine-grained tokens**
2. **Generate new token**
3. Em "Repository access", escolhe **"Only select repositories"** e
   marca o repositório de dados (ex: `siri-dados`)
4. Em "Permissions" → "Contents", marca **"Read and write"**
5. Gera o token e copia (só aparece uma vez)

## Passo 4 — Reunir os dados que faltam

| Nome                          | Onde encontrar                                                                 |
|--------------------------------|---------------------------------------------------------------------------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON`  | Conteúdo completo do arquivo `.json` da chave da conta de serviço do Drive |
| `DRIVE_BIBLIOTECA_FOLDER_ID`   | ID da pasta "Biblioteca" no Drive (trecho da URL após `/folders/`) |
| `IG_PAGE_ACCESS_TOKEN`         | Token de acesso da Página gerado pelo Usuário do Sistema "Post_BOT" (permanente) |
| `IG_BUSINESS_ACCOUNT_ID`       | `17841448125347085` |
| `SIRI_DATA_REPO`             | `seuusuario/siri-dados` (ou o repositório onde está o `siri_database.json`) |
| `SIRI_DATA_TOKEN`            | O token gerado no Passo 3 |

## Passo 5 — Cadastrar os Secrets no GitHub

No repositório deste robô: **Settings** → **Secrets and variables**
→ **Actions** → **New repository secret**, um por um, com os nomes
exatos da tabela acima.

## Passo 6 — Pasta para vídeos e projetos programados

Cria manualmente, dentro da pasta "Biblioteca" no Drive, uma subpasta
chamada **"Posts Programados"**. Vídeos avulsos (tipo "Vídeo
programado") ficam soltos direto ali; carrosséis de projeto (tipo
"Carrossel de projeto") ficam em subpastas com o nome do trabalho,
dentro dela — ex: `Biblioteca/Posts Programados/CocaCola`.

Vídeos grandes (acima de uns 50 MB) podem ter problema na hospedagem
temporária gratuita que este robô usa — se puder, exporte já em
qualidade "para redes sociais" antes de subir.

## Passo 7 — Testar manualmente

No GitHub, aba **Actions** → workflow **"Publicar post automático no
Instagram"** → **"Run workflow"**. Antes de testar, cria um post no
seu app pra hoje, com Status `Aprovado` e Plataforma `Instagram`, pra
ter algo pra publicar.

## Passo 8 — Painel visual (GitHub Pages)

Esse pacote já vem com `docs/index.html`: uma página de atalhos
(calendário, histórico de execuções, disparo manual, pasta de fotos)
pra você colar um link dela no seu app, do mesmo jeito que já faz com
o "Admin do Site".

1. Edita os 4 links de exemplo dentro de `docs/index.html` pelos
   links reais
2. No repositório: **Settings** → **Pages** → Source: branch `main`,
   pasta `/docs`
3. Em alguns minutos, o GitHub te dá uma URL pública —
   cola ela como botão no seu app

## Sobre o horário

Por padrão o robô roda **13:00 UTC (10:00 no horário de Brasília)**.
Pra mudar, edita a linha `cron` em
`.github/workflows/post-instagram.yml` (formato `minuto hora * * *`,
sempre em UTC).

## O que ainda falta (próximos passos sugeridos)

- **Limpeza automática da subpasta "Fotos Usadas"**: reduzir fotos
  antigas para thumbnails pequenos. Pode virar um segundo workflow,
  rodando 1x por mês.
- **Leitura automática dos melhores horários** via Insights do
  Instagram (`online_followers`), pra sugerir/ajustar o horário do
  post automaticamente.
