# YTGrabber

App personale per scaricare video/audio da YouTube e dagli altri siti supportati
da [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Vimeo, Twitter/X, Facebook,
Dailymotion, SoundCloud, Twitch, TikTok, Instagram e centinaia di altri).

**Uso previsto:** contenuti propri, di pubblico dominio o con licenza libera.
Scaricare contenuti protetti da copyright altrui puo' violare i termini di
servizio della piattaforma di origine — usa responsabilmente.

## Requisiti

- Python 3.10+
- FFmpeg: **incluso** nella cartella `ffmpeg/` (ffmpeg.exe, ffprobe.exe + DLL)
  per l'uso locale su Windows, nessuna installazione richiesta. Su Render
  (Linux) viene invece installato via apt nel Dockerfile.
- **aria2c** (opzionale): se installato e nel PATH, l'app lo usa
  automaticamente per download multi-connessione piu' veloci. Del tutto
  facoltativo — senza, l'app usa comunque il download parallelo nativo di
  yt-dlp.

## Installazione

```
cd "YTGrabber"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Avvio (app desktop)

```
python app.py
```

Ad ogni avvio l'app controlla in automatico se c'e' una nuova versione di
yt-dlp e la installa (silenziosamente, senza bloccare l'avvio se non c'e'
connessione).

## Avvio come server web (opzionale, uso in LAN)

```
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8756
```

Poi apri `http://<ip-del-pc>:8756` da un altro dispositivo sulla stessa rete.

## Funzionalita'

- Analisi URL: titolo, durata/numero elementi playlist, anteprima.
- Download **video**: scelta qualita' dalla piu' alta alla piu' bassa
  disponibile, output MP4 H.264/AAC compatibile con iPod Touch e iOS.
- Download **solo audio**: MP3 a 128/192/256/320 kbps, con **copertina e
  metadata incorporati** (titolo, artista) cosi' compaiono correttamente
  nella libreria dell'iPod.
- Sottotitoli opzionali incorporati nel video (IT/EN, se disponibili).
- Playlist: scarica solo l'elemento analizzato oppure l'intera playlist
  (anche in modalita' video), con indicatore "elemento X di Y" durante il
  download.
- **Login Google** per sfogliare le tue playlist ed avviarne il download con
  un click, e **cookie del browser** per accedere a playlist private/Watch
  Later senza login separato — vedi sezioni dedicate sotto.
- **Coda con piu' download in parallelo** (2 simultanei di default,
  regolabile in `backend/downloader.py` → `MAX_CONCURRENT_DOWNLOADS`),
  ciascuno annullabile singolarmente.
- **Download piu' veloci**: frammenti scaricati in parallelo, aria2c se
  disponibile, retry automatici e ripresa dei download interrotti.
- Cronologia persistente dei download con pulsante "Apri cartella".
- Notifica desktop al completamento di ogni download.
- **Auto-update di yt-dlp**: ad ogni avvio in locale, e controllo
  giornaliero automatico se ospitata come servizio web (importante: YouTube
  cambia spesso e yt-dlp si aggiorna di conseguenza — e' la cosa che piu'
  influisce sull'affidabilita' nel tempo).
- Cartella di destinazione configurabile (default `Video\YTGrabber` nella
  cartella utente; non applicabile in modalita' web, dove il file scaricato
  si ottiene con un pulsante "Scarica file").

## Formato per iPod Touch

Il video viene scaricato/unito come H.264 + AAC in contenitore MP4, il
formato nativamente supportato dall'app Video di iOS — nessuna conversione
aggiuntiva necessaria dopo il download. Per trasferirlo sull'iPod puoi
importarlo in Foto/File tramite iTunes/Finder o un'app come VLC per iOS.

## Cookie del browser (playlist private, Watch Later, contenuti con eta' ristretta)

Nella schermata di download, se hai Chrome/Edge/Firefox/Brave installati,
trovi un menu "Account per contenuti privati/con restrizioni": selezionando
un browser, yt-dlp legge la sessione YouTube gia' attiva li' per accedere
a *tuoi* contenuti privati o non in elenco (Watch Later, playlist private,
video con restrizione d'eta' a cui il tuo account ha accesso). Nessuna
configurazione richiesta — ma **chiudi il browser scelto** prima di usarlo,
altrimenti alcuni browser bloccano la lettura del file dei cookie mentre
sono aperti.

## Login "Accedi con Google" (sfoglia le tue playlist)

Per vedere le tue playlist YouTube direttamente nell'app (invece di
copiare/incollare URL), serve un client OAuth di Google — **un passaggio
che devi fare tu su Google Cloud Console** (10 minuti, gratuito), perche'
richiede il tuo account Google e non posso farlo al posto tuo:

1. Vai su [console.cloud.google.com](https://console.cloud.google.com/) e
   crea un nuovo progetto (nome libero, es. "YTGrabber personale").
2. Menu → **API e servizi → Libreria** → cerca "YouTube Data API v3" →
   **Abilita**.
3. Menu → **API e servizi → Schermata consenso OAuth** → tipo **Esterno** →
   compila i campi obbligatori (nome app, la tua email) → salva. Nella
   sezione "Utenti di test" aggiungi il tuo indirizzo Gmail.
4. Menu → **API e servizi → Credenziali → Crea credenziali → ID client
   OAuth** → tipo applicazione **App desktop** → crea.
5. Scarica il JSON delle credenziali generate e salvalo come
   `google_client_secret.json` **nella cartella principale di YTGrabber**
   (accanto a `app.py`).
6. Riavvia l'app: nella sezione "Account Google" apparira' il pulsante
   "Accedi con Google" — clicca, autorizza dal browser che si apre, e le
   tue playlist compariranno nell'app pronte per il download.

Il token di accesso resta salvato solo sul tuo PC
(`%USERPROFILE%\.ytgrabber\google_token.json`); il pulsante "Scollega" lo
rimuove. La app richiede solo permesso di **lettura** (`youtube.readonly`),
non puo' modificare o eliminare nulla sul tuo account.

## Deploy come servizio web (Render + Vercel)

L'app e' pensata per un deploy diviso: backend su Render (dove puo' girare
un processo persistente con ffmpeg), frontend statico su Vercel.

**Prima di procedere**: ospitare l'app pubblicamente la trasforma da tool
personale a servizio raggiungibile da chiunque abbia il link — un quadro
diverso, sia per il traffico/costi sia per le implicazioni legali di essere
un punto di distribuzione pubblico. Valutalo prima di rendere l'URL pubblico
a terzi.

### Backend su Render

1. Metti il progetto su un repository Git (GitHub/GitLab).
2. Su [render.com](https://render.com) → **New → Blueprint**, punta al repo:
   legge automaticamente `render.yaml` e `Dockerfile` gia' inclusi.
3. Render genera da solo un `YTGRABBER_API_KEY` casuale (variabile
   d'ambiente) — copialo, ti serve per il frontend. Senza una chiave
   impostata l'API resterebbe aperta a chiunque.
4. Ad ogni deploy l'immagine Docker installa ffmpeg via apt in automatico
   (vedi `Dockerfile`); yt-dlp si auto-aggiorna una volta al giorno da solo
   (il processo si riavvia per applicare l'update, Render lo rilancia in
   automatico).

### Frontend su Vercel

1. Apri `frontend/config.js` e imposta l'URL del servizio Render, es.
   `window.YTGRABBER_API_BASE = "https://ytgrabber-api.onrender.com";`
2. Su [vercel.com](https://vercel.com) → **New Project**, importa lo stesso
   repo, imposta la Root Directory su `frontend` (oppure lascia il
   `vercel.json` incluso, che gia' punta li').
3. Deploy. Il sito Vercel parlera' con l'API su Render usando la chiave che
   l'utente incolla nel campo "Chiave API" mostrato in pagina (salvata solo
   nel suo browser, `localStorage`).

In modalita' web l'app scarica in cartelle temporanee sul server e le
ripulisce dopo un'ora; ogni download completato offre un pulsante "Scarica
file" invece di "Apri cartella" (che non avrebbe senso su un server remoto).
Il login Google via browser locale (OAuth "Accedi con Google") resta
disponibile solo in modalita' locale — su Render dovrebbe usare un flusso
OAuth "web" con redirect configurato, non incluso in questa versione.
