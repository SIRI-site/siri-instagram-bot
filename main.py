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
     Os arquivos entram no carrossel na ORDEM NUMÉRICA do nome (ex:
     "1.jpg", "2.jpg", "3.mp4" ...) — renomeie as fotos/vídeos na
     pasta com números pra controlar a ordem final do post.
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
import re
import json
import time
import uuid
import base64
import random
import datetime
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageOps
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


def agora_em_brasilia():
    """
    Brasil não usa mais horário de verão desde 2019, então o fuso de
    Brasília é sempre UTC-3, fixo — não precisa de nenhuma biblioteca
    de fuso horário extra.
    """
    return datetime.datetime.utcnow() - datetime.timedelta(hours=3)


def buscar_posts_de_hoje():
    """
    Lê o banco de dados do app e retorna os posts de HOJE (no fuso de
    Brasília) com "Instagram" dentro da lista `plataformas`, status
    "Aprovado", e cujo horário programado já chegou (ou não tem
    horário definido, nesse caso é considerado elegível o dia todo).
    Posts em Rascunho são ignorados de propósito.

    Como o robô roda a cada 15 minutos (veja o workflow), um post cujo
    horário já passou mas ainda está "Aprovado" (por exemplo, porque a
    execução anterior falhou por instabilidade) é automaticamente
    tentado de novo na próxima execução — nada fica pra trás.

    Nota: o Facebook não precisa de checagem própria aqui — a própria
    API do Instagram já publica no Facebook automaticamente junto
    (configuração de sempre da conta). Quando TikTok/YouTube forem
    conectados no futuro, dá pra repetir o mesmo padrão: checar se a
    plataforma está na lista e chamar o publicador correspondente
    dentro do loop principal, em `main()`.
    """
    dados, _ = ler_banco_de_dados_app()
    posts = dados.get("posts", [])
    agora = agora_em_brasilia()
    hoje = agora.date().isoformat()
    hora_atual = agora.strftime("%H:%M")

    posts_de_hoje = []
    for post in posts:
        plataformas_do_post = post.get("plataformas") or []
        # Compatibilidade com posts antigos que ainda tenham o campo
        # antigo "plataforma" (texto único), caso existam no banco.
        if not plataformas_do_post and post.get("plataforma"):
            plataformas_do_post = [post.get("plataforma")]

        horario_post = (post.get("horario") or "").strip()
        horario_ja_chegou = (not horario_post) or (horario_post <= hora_atual)

        if (
            post.get("data") == hoje
            and "Instagram" in plataformas_do_post
            and post.get("status") == "Aprovado"
            and horario_ja_chegou
        ):
            posts_de_hoje.append(
                {
                    "id": post.get("id"),
                    "tipo": (post.get("tipoPost") or "carrossel").strip().lower(),
                    "legenda": post.get("legenda", ""),
                    "arquivo": (post.get("arquivoBot") or "").strip(),
                    "qtd_fotos": _parse_int_opcional(post.get("qtdFotosCarrossel")),
                }
            )
    return posts_de_hoje


def _parse_int_opcional(valor):
    """Converte pra número inteiro se possível; senão, retorna None
    (o robô usa o padrão MIN/MAX_FOTOS_CARROSSEL nesse caso)."""
    try:
        if valor is None or str(valor).strip() == "":
            return None
        return int(valor)
    except (TypeError, ValueError):
        return None


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
def listar_midias_disponiveis(drive_service, pasta_id):
    """Lista fotos E vídeos na pasta (antes só listava fotos, o que
    fazia o carrossel/story automático ignorar vídeos que estivessem
    soltos na Biblioteca)."""
    query = (
        f"'{pasta_id}' in parents "
        f"and (mimeType contains 'image/' or mimeType contains 'video/') "
        f"and trashed = false"
    )
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


def extrair_numero_ordem(nome_arquivo):
    """
    Extrai o número que aparece no INÍCIO do nome do arquivo (ex:
    "1.jpg" -> 1, "02_capa.png" -> 2, "10-final.mp4" -> 10), pra
    ordenar o carrossel de projeto na ordem que o usuário definiu
    renomeando as fotos/vídeos na pasta. Arquivos sem número no início
    vão pro final da lista, mantidos em ordem alfabética entre si.
    """
    match = re.match(r"^\s*(\d+)", nome_arquivo)
    if match:
        return (0, int(match.group(1)), nome_arquivo)
    return (1, 0, nome_arquivo)


# ---------------------------------------------------------------------------
# Compressão de imagem e vídeo (mantém os originais intactos)
# ---------------------------------------------------------------------------
# O Instagram só aceita fotos/vídeos de FEED com proporção entre 4:5
# (retrato) e 1.91:1 (paisagem). Fora desse intervalo, a API recusa a
# publicação com erro "The aspect ratio is not supported".
PROPORCAO_MINIMA_INSTAGRAM = 0.80   # 4:5 (retrato)
PROPORCAO_MAXIMA_INSTAGRAM = 1.91   # paisagem

# Já o STORY (e Reels) exige uma proporção fixa de 9:16 pra ocupar a
# tela toda — bem diferente da faixa do feed. Se a mídia não bater
# exatamente com 9:16, o próprio Instagram acrescenta faixas/"aperta"
# a imagem pra caber, em vez de preencher a tela (foi exatamente isso
# que causou as fotos/vídeos "espremidos" no story).
PROPORCAO_STORY = 9 / 16  # 0.5625


def ajustar_proporcao_instagram(img: Image.Image) -> Image.Image:
    """Corta a imagem (recorte central, sem distorcer) se a proporção
    estiver fora do intervalo aceito pelo Instagram no FEED."""
    largura, altura = img.size
    proporcao = largura / altura

    if proporcao < PROPORCAO_MINIMA_INSTAGRAM:
        # Imagem alta demais (muito "vertical"): corta em cima/embaixo.
        nova_altura = int(largura / PROPORCAO_MINIMA_INSTAGRAM)
        topo = (altura - nova_altura) // 2
        img = img.crop((0, topo, largura, topo + nova_altura))
    elif proporcao > PROPORCAO_MAXIMA_INSTAGRAM:
        # Imagem larga demais (muito "panorâmica"): corta nas laterais.
        nova_largura = int(altura * PROPORCAO_MAXIMA_INSTAGRAM)
        esquerda = (largura - nova_largura) // 2
        img = img.crop((esquerda, 0, esquerda + nova_largura, altura))

    return img


def ajustar_proporcao_story(img: Image.Image) -> Image.Image:
    """Corta a imagem (recorte central, sem distorcer) pra bater
    exatamente com 9:16, a proporção fixa exigida pelo STORY pra
    preencher a tela sem bordas."""
    largura, altura = img.size
    proporcao = largura / altura

    if abs(proporcao - PROPORCAO_STORY) < 0.002:
        return img  # já está praticamente em 9:16, não precisa cortar

    if proporcao > PROPORCAO_STORY:
        # Larga demais pro story (inclusive paisagem ou retrato "curto"
        # tipo 3:4): corta as laterais até bater com 9:16.
        nova_largura = int(altura * PROPORCAO_STORY)
        esquerda = (largura - nova_largura) // 2
        img = img.crop((esquerda, 0, esquerda + nova_largura, altura))
    else:
        # Mais alta que 9:16 (raro): corta em cima/embaixo.
        nova_altura = int(largura / PROPORCAO_STORY)
        topo = (altura - nova_altura) // 2
        img = img.crop((0, topo, largura, topo + nova_altura))

    return img


def comprimir_imagem(caminho_original: Path, caminho_saida: Path, qualidade=82, largura_max=1440, modo="feed"):
    with Image.open(caminho_original) as img:
        # Fotos de celular guardam a orientação "em pé" num metadado
        # EXIF separado, não nos pixels em si. Sem aplicar isso
        # explicitamente, a imagem processada perde essa informação e
        # sai girada. exif_transpose "grava" a rotação certa direto
        # nos pixels (e depois zera o metadado, já que não é mais
        # necessário) — sempre na posição original correta.
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img = ajustar_proporcao_story(img) if modo == "story" else ajustar_proporcao_instagram(img)
        if img.width > largura_max:
            proporcao = largura_max / img.width
            nova_altura = int(img.height * proporcao)
            img = img.resize((largura_max, nova_altura), Image.LANCZOS)
        caminho_saida.parent.mkdir(parents=True, exist_ok=True)
        img.save(caminho_saida, "JPEG", quality=qualidade, optimize=True)


def obter_dimensoes_video(caminho: Path):
    comando = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(caminho),
    ]
    resultado = subprocess.run(comando, capture_output=True, text=True, check=True)
    largura_str, altura_str = resultado.stdout.strip().split("x")
    return int(largura_str), int(altura_str)


def comprimir_video(caminho_original: Path, caminho_saida: Path, largura_max=1080, modo="feed"):
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)

    filtros = []
    try:
        largura, altura = obter_dimensoes_video(caminho_original)
        proporcao = largura / altura
        if modo == "story":
            if abs(proporcao - PROPORCAO_STORY) >= 0.002:
                if proporcao > PROPORCAO_STORY:
                    nova_largura = int(altura * PROPORCAO_STORY)
                    filtros.append(f"crop={nova_largura}:ih:(iw-{nova_largura})/2:0")
                else:
                    nova_altura = int(largura / PROPORCAO_STORY)
                    filtros.append(f"crop=iw:{nova_altura}:0:(ih-{nova_altura})/2")
        else:
            if proporcao < PROPORCAO_MINIMA_INSTAGRAM:
                nova_altura = int(largura / PROPORCAO_MINIMA_INSTAGRAM)
                filtros.append(f"crop=iw:{nova_altura}:0:(ih-{nova_altura})/2")
            elif proporcao > PROPORCAO_MAXIMA_INSTAGRAM:
                nova_largura = int(altura * PROPORCAO_MAXIMA_INSTAGRAM)
                filtros.append(f"crop={nova_largura}:ih:(iw-{nova_largura})/2:0")
    except Exception as erro:
        print(f"Aviso: não foi possível checar a proporção do vídeo ({erro}); seguindo sem recorte.")
    # O recorte (se necessário) usa as dimensões ORIGINAIS do vídeo, por
    # isso precisa vir antes do redimensionamento (scale) na cadeia de
    # filtros — senão "iw"/"ih" do crop passariam a se referir ao
    # tamanho já reduzido, e o corte sairia errado.
    filtros.append(f"scale='min({largura_max},iw)':-2")

    comando = [
        "ffmpeg", "-y",
        "-i", str(caminho_original),
        "-vf", ",".join(filtros),
        "-c:v", "libx264", "-crf", "26", "-preset", "veryfast",
        # Alguns vídeos de celular têm ritmo de quadros irregular
        # (varia levemente entre um quadro e outro) — isso já apareceu
        # nos logs como "dup"/"drop" durante a compressão. Forçar saída
        # com ritmo constante evita isso, e é um gatilho conhecido de
        # falhas de processamento no Instagram (erro 2207076).
        "-fps_mode", "cfr",
        # O vídeo original tinha uma marcação de espaço de cor antiga
        # e incomum (bt470bg, padrão de TV analógica) em vez do padrão
        # atual de vídeos de celular/streaming (bt709) — isso também
        # é apontado como possível gatilho de falha silenciosa no
        # processamento de vídeo por algumas plataformas. Forçando a
        # marcação padrão aqui, por precaução.
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "128k",
        str(caminho_saida),
    ]
    subprocess.run(comando, check=True)


def eh_video(nome_arquivo: str) -> bool:
    return Path(nome_arquivo).suffix.lower() in EXTENSOES_VIDEO


EXTENSAO_POR_MIMETYPE = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/x-matroska": ".mkv",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heic",
}


def extensao_por_mimetype(mime_type):
    if mime_type in EXTENSAO_POR_MIMETYPE:
        return EXTENSAO_POR_MIMETYPE[mime_type]
    if (mime_type or "").startswith("video/"):
        return ".mp4"
    if (mime_type or "").startswith("image/"):
        return ".jpg"
    return ""


def nome_local_seguro(item):
    """
    Garante que o arquivo local sempre tenha uma extensão reconhecida
    (necessária pro ffmpeg saber qual formato gerar na saída, e pro
    Pillow identificar corretamente uma imagem) — mesmo quando o
    arquivo foi salvo no Drive sem nenhuma extensão no nome (ex:
    "RECORDAR" em vez de "RECORDAR.mp4"). A extensão certa é deduzida
    a partir do mimeType real do arquivo, informado pelo próprio
    Drive, não do nome que a pessoa deu a ele.
    """
    nome = item.get("name", "arquivo")
    if Path(nome).suffix:
        return nome
    return nome + extensao_por_mimetype(item.get("mimeType", ""))


def eh_video_item(item):
    """
    Determina se um item do Drive é vídeo com base no mimeType real
    (mais confiável do que só olhar a extensão do nome do arquivo,
    que às vezes vem vazia).
    """
    mime_type = item.get("mimeType", "") or ""
    if mime_type.startswith("video/"):
        return True
    if mime_type.startswith("image/"):
        return False
    # Sem mimeType reconhecível: cai pro método antigo (extensão do nome).
    return eh_video(item.get("name", ""))


# ---------------------------------------------------------------------------
# Publicar as mídias comprimidas no repositório GitHub (URL pública)
# ---------------------------------------------------------------------------
def sanitizar_nome_arquivo(texto):
    """
    Remove acentos e qualquer caractere que não seja letra/número/traço,
    pra garantir que o nome do arquivo publicado seja sempre seguro
    tanto pro Git quanto pra virar URL pública (a Meta falha ao buscar
    a mídia se a URL tiver caracteres não-ASCII sem codificação, como
    aconteceu com uma pasta chamada "galpão").
    """
    import unicodedata
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", sem_acento)


def publicar_midia_no_github(caminho_comprimido: Path, prefixo: str):
    """
    Copia a mídia comprimida pra pasta pública do repositório, commita
    e envia. O nome do arquivo já inclui um sufixo aleatório curto
    (uuid) além do prefixo, pra nunca colidir com o nome de outro post
    — mesmo que dois posts usem exatamente a mesma foto de origem. O
    prefixo é sanitizado (sem acentos/caracteres especiais) e a URL
    final é codificada, pra nunca falhar por causa do nome de uma
    pasta ou arquivo com acento, espaço, etc.

    Se, ainda assim, o Git disser que "não há nada para commitar"
    (por exemplo, coincidência rara de conteúdo idêntico já
    existente), isso NÃO é tratado como erro: o arquivo já está lá com
    o conteúdo certo, e a publicação segue normalmente.
    """
    PUBLIC_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    prefixo_seguro = sanitizar_nome_arquivo(prefixo)
    sufixo_unico = uuid.uuid4().hex[:8]
    nome_final = f"{prefixo_seguro}_{sufixo_unico}{caminho_comprimido.suffix.lower()}"
    destino = PUBLIC_ASSETS_DIR / nome_final
    destino.write_bytes(caminho_comprimido.read_bytes())

    subprocess.run(["git", "add", str(destino)], check=True)

    resultado_commit = subprocess.run(
        ["git", "commit", "-m", f"Mídia do post: {nome_final}"],
        capture_output=True, text=True,
    )
    if resultado_commit.returncode != 0:
        saida = (resultado_commit.stdout or "") + (resultado_commit.stderr or "")
        if "nothing to commit" in saida or "nada a submeter" in saida.lower():
            print(f"Aviso: nada novo para commitar em {nome_final} (arquivo já existia idêntico). Seguindo normalmente.")
        else:
            print("Saída do git commit:", saida)
            raise RuntimeError(f"Falha ao commitar {nome_final}: {saida}")
    else:
        subprocess.run(["git", "push"], check=True)

    return f"https://cdn.jsdelivr.net/gh/{GITHUB_REPOSITORY}@main/{PUBLIC_ASSETS_DIR}/{quote(nome_final)}"


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
            # "status" (texto legível) além de "status_code" (código),
            # pra conseguir mostrar o motivo real quando der ERROR, em
            # vez de só saber que deu erro sem saber o porquê.
            params={"fields": "status_code,status", "access_token": IG_PAGE_ACCESS_TOKEN},
        )
        resp.raise_for_status()
        dados = resp.json()
        status_code = dados.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            detalhe = dados.get("status") or "(a Meta não informou detalhe adicional)"
            raise RuntimeError(f"Falha ao processar mídia {creation_id} no Instagram. Detalhe da Meta: {detalhe}")
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
def publicar_carrossel_automatico(drive_service, legenda, pasta_fotos_usadas_id, qtd_desejada=None):
    midias = listar_midias_disponiveis(drive_service, DRIVE_BIBLIOTECA_FOLDER_ID)
    if len(midias) < MIN_FOTOS_CARROSSEL:
        raise RuntimeError(
            f"Só há {len(midias)} arquivo(s) na Biblioteca; são necessários pelo menos {MIN_FOTOS_CARROSSEL}."
        )

    if qtd_desejada:
        # Post definiu sua própria quantidade de itens; respeita esse
        # número, mas nunca menos que o mínimo nem mais do que existe.
        qtd = max(MIN_FOTOS_CARROSSEL, min(qtd_desejada, len(midias)))
    else:
        qtd = max(MIN_FOTOS_CARROSSEL, min(MAX_FOTOS_CARROSSEL, len(midias)))

    escolhidas = random.sample(midias, qtd)

    creation_ids = []
    for i, item in enumerate(escolhidas):
        nome_local = nome_local_seguro(item)
        original = TEMP_DIR / "originais" / nome_local
        comprimida = TEMP_DIR / "comprimidas" / nome_local
        baixar_arquivo(drive_service, item["id"], original)

        video = eh_video_item(item)
        if video:
            comprimir_video(original, comprimida)
        else:
            comprimir_imagem(original, comprimida)

        url = publicar_midia_no_github(comprimida, f"carrossel_{agora_em_brasilia().date().isoformat()}_{i}")
        # Dentro de um carrossel, um item de vídeo precisa do
        # media_type "REELS" (não "VIDEO") — é uma exigência específica
        # da API pra itens de carrossel, diferente de um vídeo avulso.
        media_type = "REELS" if video else "IMAGE"
        campo = "video_url" if video else "image_url"
        creation_id = criar_container(media_type, campo, url, is_carousel_item=True)
        # Espera o processamento terminar de verdade (não só a criação
        # do container) antes de considerar esse item pronto — vale
        # tanto pra vídeo quanto pra foto, evita publicar um carrossel
        # com item "fantasma" que não chegou a processar a tempo.
        aguardar_processamento(creation_id)
        creation_ids.append(creation_id)

    container_id = criar_container("CAROUSEL", "children", ",".join(creation_ids), legenda=legenda)
    resultado = publicar_container(container_id)
    print("Carrossel publicado:", resultado)

    for item in escolhidas:
        mover_arquivo(drive_service, item["id"], pasta_fotos_usadas_id)
        print(f"Original movido para Fotos Usadas: {item['name']}")


def publicar_story_automatico(drive_service, legenda, arquivo_indicado, pasta_fotos_usadas_id):
    if arquivo_indicado:
        item = buscar_arquivo_por_nome(drive_service, arquivo_indicado, DRIVE_BIBLIOTECA_FOLDER_ID)
    else:
        midias = listar_midias_disponiveis(drive_service, DRIVE_BIBLIOTECA_FOLDER_ID)
        if not midias:
            raise RuntimeError("Não há fotos nem vídeos na Biblioteca para o story.")
        item = random.choice(midias)

    nome_local = nome_local_seguro(item)
    original = TEMP_DIR / "originais" / nome_local
    baixar_arquivo(drive_service, item["id"], original)

    video = eh_video_item(item)
    comprimida = TEMP_DIR / "comprimidas" / nome_local
    if video:
        comprimir_video(original, comprimida, modo="story")
    else:
        comprimir_imagem(original, comprimida, modo="story")

    url = publicar_midia_no_github(comprimida, f"story_{agora_em_brasilia().date().isoformat()}")

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
    """
    Usada tanto pro tipo 'video' quanto pro tipo 'reels' — na prática
    são a mesma coisa pro Instagram: esse post SEMPRE é publicado
    através do endpoint de REELS da Meta (media_type="REELS"), que
    exige proporção fixa 9:16 pra preencher a tela toda — a mesma
    exigência do Story. Por isso a compressão usa modo="story" aqui,
    não a faixa mais larga do feed.
    """
    if not arquivo_indicado:
        raise RuntimeError("Esse tipo de post precisa do nome do arquivo em 'arquivoBot'.")

    item = buscar_arquivo_por_nome(drive_service, arquivo_indicado, pasta_programados_id)

    nome_local = nome_local_seguro(item)
    original = TEMP_DIR / "originais" / nome_local
    comprimida = TEMP_DIR / "comprimidas" / nome_local
    baixar_arquivo(drive_service, item["id"], original)
    comprimir_video(original, comprimida, modo="story")

    url = publicar_midia_no_github(comprimida, f"video_{agora_em_brasilia().date().isoformat()}")

    print("Aguardando a CDN atualizar...")
    time.sleep(20)

    creation_id = criar_container("REELS", "video_url", url, legenda=legenda)
    aguardar_processamento(creation_id)
    resultado = publicar_container(creation_id)
    print("Vídeo/Reels publicado:", resultado)

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

    # Ordena pelos números no início do nome do arquivo (1.jpg, 2.jpg,
    # 3.mp4...), pra respeitar a ordem que o usuário definiu na pasta.
    itens.sort(key=lambda item: extrair_numero_ordem(item["name"]))

    if len(itens) > 10:
        print(f"Aviso: a pasta tem {len(itens)} arquivos; o Instagram só aceita 10 por carrossel — usando só os 10 primeiros na ordem definida.")
    itens = itens[:10]  # limite do Instagram para carrossel
    print("Ordem do carrossel:", ", ".join(item["name"] for item in itens))

    creation_ids = []
    for i, item in enumerate(itens):
        nome_local = nome_local_seguro(item)
        original = TEMP_DIR / "originais" / nome_local
        comprimida = TEMP_DIR / "comprimidas" / nome_local
        baixar_arquivo(drive_service, item["id"], original)

        video = eh_video_item(item)
        if video:
            comprimir_video(original, comprimida)
        else:
            comprimir_imagem(original, comprimida)

        url = publicar_midia_no_github(
            comprimida, f"projeto_{nome_pasta}_{agora_em_brasilia().date().isoformat()}_{i}"
        )
        # Dentro de um carrossel, um item de vídeo precisa do
        # media_type "REELS" (não "VIDEO") — exigência específica da
        # API pra itens de carrossel, diferente de um vídeo avulso.
        media_type = "REELS" if video else "IMAGE"
        campo = "video_url" if video else "image_url"
        creation_id = criar_container(media_type, campo, url, is_carousel_item=True)
        # Espera confirmação de verdade (status FINISHED) pra qualquer
        # item, não só vídeo — foi exatamente a falta dessa confirmação
        # pra fotos que causou um carrossel de projeto ser marcado como
        # publicado sem realmente aparecer no Instagram.
        aguardar_processamento(creation_id)
        creation_ids.append(creation_id)

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
            elif post["tipo"] in ("video", "reels"):
                publicar_video_programado(
                    drive_service, post["legenda"], post["arquivo"], pasta_programados_id, pasta_publicados_id
                )
            elif post["tipo"] == "projeto":
                publicar_carrossel_curado(
                    drive_service, post["legenda"], post["arquivo"], pasta_programados_id, pasta_publicados_id
                )
            else:  # "carrossel" (padrão)
                publicar_carrossel_automatico(
                    drive_service, post["legenda"], pasta_fotos_usadas_id, qtd_desejada=post.get("qtd_fotos")
                )

            marcar_post_como_publicado_no_app(post["id"])

        except Exception as erro:
            print(f"Erro ao processar post {post['id']}: {erro}")
            # Não marca como Publicado — o post continua "Aprovado" e
            # será tentado de novo na próxima execução do robô.
            continue


if __name__ == "__main__":
    main()