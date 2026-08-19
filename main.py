"""
SIRI - Robô de publicação automática no Instagram
====================================================
Fluxo:
1. Lê o arquivo `siri_database.json` (o mesmo banco de dados que o app
   de orçamento da S.I.R.I. já usa) direto do GitHub, via API — SEM
   nunca escrever no restante do arquivo, só no campo "status" do post
   publicado, e sempre buscando a versão mais recente antes de gravar
   (evita conflito com o autosave do app).
2. Filtra os posts de HOJE, com "Instagram" dentro da lista de
   plataformas selecionadas no post, e status "Aprovado" (posts em
   Rascunho são ignorados de propósito). O Facebook não precisa de
   checagem própria — a publicação no Instagram já reflete lá
   automaticamente. TikTok e YouTube ainda não são publicados por
   este robô (fica pra quando forem conectados).
3. Para cada post encontrado, dependendo do "tipoPost":
   - "carrossel" (padrão): escolhe fotos aleatórias da pasta
     "Biblioteca" do Drive e publica um carrossel no feed.
   - "story": escolhe uma foto (aleatória, ou a indicada em
     "arquivoBot") e publica como story.
   - "video": usa o vídeo indicado em "arquivoBot" (dentro de
     "Biblioteca/Posts Programados") e publica no feed.
   - "projeto": publica um carrossel com tudo que estiver na subpasta
     indicada em "arquivoBot" (dentro de "Posts Programados") — pra
     trabalhos com curadoria manual (fotos + vídeo de um cliente).
4. Comprime as imagens/vídeos antes de publicar (originais no Drive
   nunca são alterados).
5. Move os arquivos ORIGINAIS usados: fotos aleatórias vão para
   "Biblioteca/Fotos Usadas"; conteúdo de "Posts Programados" vai para
   "Posts Programados/Publicados".
6. Marca o post como "Publicado" de volta no siri_database.json,
   usando o sha mais recente do arquivo no momento da escrita.

Todas as credenciais vêm de variáveis de ambiente (GitHub Secrets).
"""

import os
import io
import json
import time
import base64
import random
import datetime
import subprocess
from pathlib import Path

import requests
from PIL import Image
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ---------------------------------------------------------------------------
# Configurações vindas do ambiente (GitHub Secrets)
# ---------------------------------------------------------------------------
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
DRIVE_BIBLIOTECA_FOLDER_ID = os.environ["DRIVE_BIBLIOTECA_FOLDER_ID"]
IG_PAGE_ACCESS_TOKEN = os.environ["IG_PAGE_ACCESS_TOKEN"]
IG_BUSINESS_ACCOUNT_ID = os.environ["IG_BUSINESS_ACCOUNT_ID"]

# Repositório onde as imagens/vídeos comprimidos são publicados
# temporariamente (para virarem URL pública, exigida pela API do
# Instagram). Pode ser o mesmo repositório deste robô.
# Formato: "usuario/repositorio"
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

# Repositório + caminho do arquivo siri_database.json (o banco de
# dados do app de orçamento). Recomendado: um repositório PRIVADO
# separado do código do app, para que salvar dados nunca dispare um
# novo deploy na Vercel. Ex: "seuusuario/siri-dados"
SIRI_DATA_REPO = os.environ["SIRI_DATA_REPO"]
SIRI_DATA_PATH = os.environ.get("SIRI_DATA_PATH", "siri_database.json")
SIRI_DATA_TOKEN = os.environ["SIRI_DATA_TOKEN"]

# Quantas fotos no máximo/mínimo entram em cada carrossel automático
MAX_FOTOS_CARROSSEL = int(os.environ.get("MAX_FOTOS_CARROSSEL", "5"))
MIN_FOTOS_CARROSSEL = int(os.environ.get("MIN_FOTOS_CARROSSEL", "2"))

TEMP_DIR = Path("temp_publicacao")
PUBLIC_ASSETS_DIR = Path("posts_temp")
FOTOS_USADAS_SUBPASTA = "Fotos Usadas"
POSTS_PROGRAMADOS_SUBPASTA = "Posts Programados"
POSTS_PROGRAMADOS_PUBLICADOS_SUBPASTA = "Publicados"

EXTENSOES_VIDEO = {".mp4", ".mov", ".m4v"}

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

GITHUB_API_BASE = "https://api.github.com"


# ---------------------------------------------------------------------------
# GitHub: ler e atualizar o siri_database.json (sem tocar no resto)
# ---------------------------------------------------------------------------
def ler_banco_de_dados_app():
    """Retorna (dados_decodificados: dict, sha_atual: str)."""
    url = f"{GITHUB_API_BASE}/repos/{SIRI_DATA_REPO}/contents/{SIRI_DATA_PATH}"
    headers = {
        "Authorization": f"Bearer {SIRI_DATA_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.get(url, headers=headers, params={"nocache": str(time.time())})
    resp.raise_for_status()
    payload = resp.json()
    conteudo = base64.b64decode(payload["content"]).decode("utf-8")
    return json.loads(conteudo), payload["sha"]


def marcar_post_como_publicado_no_app(post_id, tentativas=5):
    """
    Busca a versão MAIS RECENTE do arquivo bem na hora de gravar (para
    não sobrescrever nenhuma edição feita no app entre a leitura inicial
    e agora), altera só o status do post com esse id para "Publicado",
    e tenta salvar. Se outra gravação aconteceu nesse meio tempo (sha
    não bate mais), tenta de novo do zero, até `tentativas` vezes.
    """
    for tentativa in range(tentativas):
        dados, sha_atual = ler_banco_de_dados_app()
        posts = dados.get("posts", [])
        encontrado = False
        for post in posts:
            if str(post.get("id")) == str(post_id):
                post["status"] = "Publicado"
                encontrado = True
                break
        if not encontrado:
            print(f"Aviso: post {post_id} não encontrado no banco na hora de marcar como publicado.")
            return

        conteudo_novo = json.dumps(dados, ensure_ascii=False, indent=2)
        conteudo_base64 = base64.b64encode(conteudo_novo.encode("utf-8")).decode("utf-8")

        url = f"{GITHUB_API_BASE}/repos/{SIRI_DATA_REPO}/contents/{SIRI_DATA_PATH}"
        headers = {
            "Authorization": f"Bearer {SIRI_DATA_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        body = {
            "message": f"Post_BOT: marcou post {post_id} como Publicado",
            "content": conteudo_base64,
            "sha": sha_atual,
        }
        resp = requests.put(url, headers=headers, json=body)
        if resp.status_code in (200, 201):
            print(f"Post {post_id} marcado como Publicado no app.")
            return
        if resp.status_code == 409:
            print(f"Conflito de versão ao salvar (tentativa {tentativa + 1}/{tentativas}); tentando de novo...")
            time.sleep(3)
            continue
        resp.raise_for_status()

    raise RuntimeError(f"Não foi possível marcar o post {post_id} como Publicado após {tentativas} tentativas.")


def buscar_posts_de_hoje():
    """
    Lê o banco de dados do app e retorna os posts agendados para hoje,
    com "Instagram" dentro da lista `plataformas` e status "Aprovado"
    (posts em Rascunho são ignorados de propósito).

    Nota: o Facebook não precisa de checagem própria aqui — a própria
    API do Instagram já publica no Facebook automaticamente junto
    (configuração de sempre da conta). Quando TikTok/YouTube forem
    conectados no futuro, dá pra repetir o mesmo padrão: checar se a
    plataforma está na lista e chamar o publicador correspondente
    dentro do loop principal, em `main()`.
    """
    dados, _ = ler_banco_de_dados_app()
    posts = dados.get("posts", [])
    hoje = datetime.date.today().isoformat()

    posts_de_hoje = []
    for post in posts:
        plataformas_do_post = post.get("plataformas") or []
        # Compatibilidade com posts antigos que ainda tenham o campo
        # antigo "plataforma" (texto único), caso existam no banco.
        if not plataformas_do_post and post.get("plataforma"):
            plataformas_do_post = [post.get("plataforma")]

        if (
            post.get("data") == hoje
            and "Instagram" in plataformas_do_post
            and post.get("status") == "Aprovado"
        ):
            posts_de_hoje.append(
                {
                    "id": post.get("id"),
                    "tipo": (post.get("tipoPost") or "carrossel").strip().lower(),
                    "legenda": post.get("legenda", ""),
                    "arquivo": (post.get("arquivoBot") or "").strip(),
                }
            )
    return posts_de_hoje


# ---------------------------------------------------------------------------
# Autenticação Google (Drive)
# ---------------------------------------------------------------------------
def get_drive_service():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Drive: subpastas (cria se não existir)
# ---------------------------------------------------------------------------
def obter_ou_criar_subpasta(drive_service, nome, pasta_pai_id):
    query = (
        f"'{pasta_pai_id}' in parents "
        f"and name = '{nome}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    resultado = drive_service.files().list(q=query, fields="files(id, name)").execute()
    pastas = resultado.get("files", [])
    if pastas:
        return pastas[0]["id"]
    metadata = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [pasta_pai_id],
    }
    pasta = drive_service.files().create(body=metadata, fields="id").execute()
    return pasta["id"]


# ---------------------------------------------------------------------------
# Drive: listar/buscar arquivos
# ---------------------------------------------------------------------------
def listar_fotos_disponiveis(drive_service, pasta_id):
    query = f"'{pasta_id}' in parents and mimeType contains 'image/' and trashed = false"
    resultado = (
        drive_service.files()
        .list(q=query, fields="files(id, name, mimeType)", pageSize=1000)
        .execute()
    )
    return resultado.get("files", [])


def buscar_arquivo_por_nome(drive_service, nome_exato, pasta_id):
    query = f"'{pasta_id}' in parents and name = '{nome_exato}' and trashed = false"
    resultado = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    arquivos = resultado.get("files", [])
    if not arquivos:
        raise FileNotFoundError(f"Arquivo '{nome_exato}' não encontrado na pasta esperada.")
    return arquivos[0]


def baixar_arquivo(drive_service, file_id, destino: Path):
    request = drive_service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as f:
        f.write(buffer.getvalue())


def mover_arquivo(drive_service, file_id, pasta_destino_id):
    arquivo = drive_service.files().get(fileId=file_id, fields="parents").execute()
    parents_antigos = ",".join(arquivo.get("parents", []))
    drive_service.files().update(
        fileId=file_id,
        addParents=pasta_destino_id,
        removeParents=parents_antigos,
        fields="id, parents",
    ).execute()


# ---------------------------------------------------------------------------
# Compressão de imagem e vídeo (mantém os originais intactos)
# ---------------------------------------------------------------------------
def comprimir_imagem(caminho_original: Path, caminho_saida: Path, qualidade=82, largura_max=1440):
    with Image.open(caminho_original) as img:
        img = img.convert("RGB")
        if img.width > largura_max:
            proporcao = largura_max / img.width
            nova_altura = int(img.height * proporcao)
            img = img.resize((largura_max, nova_altura), Image.LANCZOS)
        caminho_saida.parent.mkdir(parents=True, exist_ok=True)
        img.save(caminho_saida, "JPEG", quality=qualidade, optimize=True)


def comprimir_video(caminho_original: Path, caminho_saida: Path, largura_max=1080):
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    comando = [
        "ffmpeg", "-y",
        "-i", str(caminho_original),
        "-vf", f"scale='min({largura_max},iw)':-2",
        "-c:v", "libx264", "-crf", "26", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        str(caminho_saida),
    ]
    subprocess.run(comando, check=True)


def eh_video(nome_arquivo: str) -> bool:
    return Path(nome_arquivo).suffix.lower() in EXTENSOES_VIDEO


# ---------------------------------------------------------------------------
# Publicar as mídias comprimidas no repositório GitHub (URL pública)
# ---------------------------------------------------------------------------
def publicar_midia_no_github(caminho_comprimido: Path, prefixo: str):
    PUBLIC_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    nome_final = f"{prefixo}{caminho_comprimido.suffix.lower()}"
    destino = PUBLIC_ASSETS_DIR / nome_final
    destino.write_bytes(caminho_comprimido.read_bytes())

    subprocess.run(["git", "add", str(destino)], check=True)
    subprocess.run(["git", "commit", "-m", f"Mídia do post: {nome_final}"], check=True)
    subprocess.run(["git", "push"], check=True)

    return f"https://cdn.jsdelivr.net/gh/{GITHUB_REPOSITORY}@main/{PUBLIC_ASSETS_DIR}/{nome_final}"


def configurar_git():
    subprocess.run(["git", "config", "user.name", "siri-post-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "bot@siri.local"], check=True)


# ---------------------------------------------------------------------------
# Instagram Graph API
# ---------------------------------------------------------------------------
def criar_container(media_type, url_campo, url_valor, legenda=None, is_carousel_item=False):
    data = {"access_token": IG_PAGE_ACCESS_TOKEN, url_campo: url_valor}
    if media_type:
        data["media_type"] = media_type
    if legenda:
        data["caption"] = legenda
    if is_carousel_item:
        data["is_carousel_item"] = "true"
    resp = requests.post(f"{GRAPH_BASE}/{IG_BUSINESS_ACCOUNT_ID}/media", data=data)
    if not resp.ok:
        print("Resposta de erro da Meta:", resp.text)
    resp.raise_for_status()
    return resp.json()["id"]


def aguardar_processamento(creation_id, tentativas=30, intervalo=10):
    for _ in range(tentativas):
        resp = requests.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": IG_PAGE_ACCESS_TOKEN},
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Falha ao processar mídia {creation_id} no Instagram.")
        time.sleep(intervalo)
    raise TimeoutError(f"Tempo esgotado esperando o processamento de {creation_id}.")


def publicar_container(creation_id):
    resp = requests.post(
        f"{GRAPH_BASE}/{IG_BUSINESS_ACCOUNT_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_PAGE_ACCESS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Publicadores por tipo de post
# ---------------------------------------------------------------------------
def publicar_carrossel_automatico(drive_service, legenda, pasta_fotos_usadas_id):
    fotos = listar_fotos_disponiveis(drive_service, DRIVE_BIBLIOTECA_FOLDER_ID)
    if len(fotos) < MIN_FOTOS_CARROSSEL:
        raise RuntimeError(
            f"Só há {len(fotos)} foto(s) na Biblioteca; são necessárias pelo menos {MIN_FOTOS_CARROSSEL}."
        )
    qtd = max(MIN_FOTOS_CARROSSEL, min(MAX_FOTOS_CARROSSEL, len(fotos)))
    escolhidas = random.sample(fotos, qtd)

    creation_ids = []
    for i, foto in enumerate(escolhidas):
        original = TEMP_DIR / "originais" / foto["name"]
        comprimida = TEMP_DIR / "comprimidas" / foto["name"]
        baixar_arquivo(drive_service, foto["id"], original)
        comprimir_imagem(original, comprimida)
        url = publicar_midia_no_github(comprimida, f"carrossel_{datetime.date.today().isoformat()}_{i}")
        creation_ids.append(criar_container("IMAGE", "image_url", url, is_carousel_item=True))

    print("Aguardando a CDN atualizar...")
    time.sleep(20)

    container_id = criar_container("CAROUSEL", "children", ",".join(creation_ids), legenda=legenda)
    resultado = publicar_container(container_id)
    print("Carrossel publicado:", resultado)

    for foto in escolhidas:
        mover_arquivo(drive_service, foto["id"], pasta_fotos_usadas_id)
        print(f"Original movido para Fotos Usadas: {foto['name']}")


def publicar_story_automatico(drive_service, legenda, arquivo_indicado, pasta_fotos_usadas_id):
    if arquivo_indicado:
        item = buscar_arquivo_por_nome(drive_service, arquivo_indicado, DRIVE_BIBLIOTECA_FOLDER_ID)
    else:
        fotos = listar_fotos_disponiveis(drive_service, DRIVE_BIBLIOTECA_FOLDER_ID)
        if not fotos:
            raise RuntimeError("Não há fotos na Biblioteca para o story.")
        item = random.choice(fotos)

    original = TEMP_DIR / "originais" / item["name"]
    baixar_arquivo(drive_service, item["id"], original)

    video = eh_video(item["name"])
    comprimida = TEMP_DIR / "comprimidas" / item["name"]
    if video:
        comprimir_video(original, comprimida)
    else:
        comprimir_imagem(original, comprimida)

    url = publicar_midia_no_github(comprimida, f"story_{datetime.date.today().isoformat()}")

    print("Aguardando a CDN atualizar...")
    time.sleep(20)

    campo = "video_url" if video else "image_url"
    creation_id = criar_container("STORIES", campo, url)
    if video:
        aguardar_processamento(creation_id)

    resultado = publicar_container(creation_id)
    print("Story publicado:", resultado)

    mover_arquivo(drive_service, item["id"], pasta_fotos_usadas_id)
    print(f"Original movido para Fotos Usadas: {item['name']}")


def publicar_video_programado(drive_service, legenda, arquivo_indicado, pasta_programados_id, pasta_publicados_id):
    if not arquivo_indicado:
        raise RuntimeError("Post do tipo 'video' precisa do nome do arquivo em 'arquivoBot'.")

    item = buscar_arquivo_por_nome(drive_service, arquivo_indicado, pasta_programados_id)

    original = TEMP_DIR / "originais" / item["name"]
    comprimida = TEMP_DIR / "comprimidas" / item["name"]
    baixar_arquivo(drive_service, item["id"], original)
    comprimir_video(original, comprimida)

    url = publicar_midia_no_github(comprimida, f"video_{datetime.date.today().isoformat()}")

    print("Aguardando a CDN atualizar...")
    time.sleep(20)

    creation_id = criar_container("REELS", "video_url", url, legenda=legenda)
    aguardar_processamento(creation_id)
    resultado = publicar_container(creation_id)
    print("Vídeo programado publicado:", resultado)

    mover_arquivo(drive_service, item["id"], pasta_publicados_id)
    print(f"Original movido para Posts Programados/Publicados: {item['name']}")


def publicar_carrossel_curado(drive_service, legenda, nome_pasta, pasta_programados_id, pasta_publicados_id):
    if not nome_pasta:
        raise RuntimeError("Post do tipo 'projeto' precisa do nome da subpasta em 'arquivoBot'.")

    query = (
        f"'{pasta_programados_id}' in parents "
        f"and name = '{nome_pasta}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    resultado = drive_service.files().list(q=query, fields="files(id, name)").execute()
    pastas = resultado.get("files", [])
    if not pastas:
        raise FileNotFoundError(f"Subpasta '{nome_pasta}' não encontrada dentro de Posts Programados.")
    pasta_projeto_id = pastas[0]["id"]

    itens = (
        drive_service.files()
        .list(
            q=f"'{pasta_projeto_id}' in parents and trashed = false",
            fields="files(id, name, mimeType)",
            pageSize=1000,
        )
        .execute()
        .get("files", [])
    )
    if len(itens) < 2:
        raise RuntimeError(f"A subpasta '{nome_pasta}' precisa ter pelo menos 2 arquivos para virar carrossel.")
    itens = itens[:10]  # limite do Instagram para carrossel

    creation_ids = []
    for i, item in enumerate(itens):
        original = TEMP_DIR / "originais" / item["name"]
        comprimida = TEMP_DIR / "comprimidas" / item["name"]
        baixar_arquivo(drive_service, item["id"], original)

        video = eh_video(item["name"])
        if video:
            comprimir_video(original, comprimida)
        else:
            comprimir_imagem(original, comprimida)

        url = publicar_midia_no_github(
            comprimida, f"projeto_{nome_pasta}_{datetime.date.today().isoformat()}_{i}"
        )
        media_type = "VIDEO" if video else "IMAGE"
        campo = "video_url" if video else "image_url"
        creation_id = criar_container(media_type, campo, url, is_carousel_item=True)
        if video:
            aguardar_processamento(creation_id)
        creation_ids.append(creation_id)

    print("Aguardando a CDN atualizar...")
    time.sleep(20)

    container_id = criar_container("CAROUSEL", "children", ",".join(creation_ids), legenda=legenda)
    resultado = publicar_container(container_id)
    print(f"Carrossel do projeto '{nome_pasta}' publicado:", resultado)

    mover_arquivo(drive_service, pasta_projeto_id, pasta_publicados_id)
    print(f"Subpasta '{nome_pasta}' movida para Posts Programados/Publicados.")


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------
def main():
    posts_de_hoje = buscar_posts_de_hoje()
    if not posts_de_hoje:
        print("Nenhum post Aprovado para o Instagram hoje. Encerrando sem publicar.")
        return

    print(f"{len(posts_de_hoje)} post(s) aprovado(s) para hoje.")

    drive_service = get_drive_service()
    configurar_git()

    pasta_fotos_usadas_id = obter_ou_criar_subpasta(drive_service, FOTOS_USADAS_SUBPASTA, DRIVE_BIBLIOTECA_FOLDER_ID)
    pasta_programados_id = obter_ou_criar_subpasta(drive_service, POSTS_PROGRAMADOS_SUBPASTA, DRIVE_BIBLIOTECA_FOLDER_ID)
    pasta_publicados_id = obter_ou_criar_subpasta(drive_service, POSTS_PROGRAMADOS_PUBLICADOS_SUBPASTA, pasta_programados_id)

    for post in posts_de_hoje:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"\n--- Processando post {post['id']} (tipo: {post['tipo']}) ---")
        try:
            if post["tipo"] == "story":
                publicar_story_automatico(drive_service, post["legenda"], post["arquivo"], pasta_fotos_usadas_id)
            elif post["tipo"] == "video":
                publicar_video_programado(
                    drive_service, post["legenda"], post["arquivo"], pasta_programados_id, pasta_publicados_id
                )
            elif post["tipo"] == "projeto":
                publicar_carrossel_curado(
                    drive_service, post["legenda"], post["arquivo"], pasta_programados_id, pasta_publicados_id
                )
            else:  # "carrossel" (padrão)
                publicar_carrossel_automatico(drive_service, post["legenda"], pasta_fotos_usadas_id)

            marcar_post_como_publicado_no_app(post["id"])

        except Exception as erro:
            print(f"Erro ao processar post {post['id']}: {erro}")
            # Não marca como Publicado — o post continua "Aprovado" e
            # será tentado de novo na próxima execução do robô.
            continue


if __name__ == "__main__":
    main()
