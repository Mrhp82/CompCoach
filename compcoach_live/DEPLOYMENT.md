# CompCoach: dal Codespace al link stabile

Questa guida mantiene il codice nella sottocartella `compcoach_live` del
repository. Supabase conserva dati, storico, loghi e mappe; l'hosting Streamlit
esegue l'app e fornisce il link da aprire dal telefono. Chiudere il Codespace
non fermerà l'app pubblicata sull'hosting.

Il codice è predisposto per il collegamento. La pubblicazione e il trasferimento
dei dati richiedono ancora un progetto Supabase e le credenziali private del
tuo account. Non inviare password o chiavi nelle chat, negli screenshot o su
GitHub.

## 1. Continua i test attuali

Dalla cartella principale del repository:

```bash
python -m pip install -r compcoach_live/requirements.txt
bash compcoach_live/launch.sh
```

Se sei già dentro `compcoach_live`:

```bash
python -m pip install -r requirements.txt
bash launch.sh
```

In Codespaces apri la porta **8501** da **Ports**. Questi test usano ancora
SQLite finché non configuri il database cloud. Avvia solo CompCoach; l'altra
FencingAPP può rimanere aperta in un Codespace o terminale separato.

## 2. Crea un progetto Supabase dedicato

1. Apri [Supabase](https://supabase.com/dashboard), con il tuo account abituale.
2. Scegli **New project** e un nome riconoscibile, per esempio **CompCoach**.
   Usa un progetto separato da quello della FencingAPP interna.
   Per iniziare senza costi aggiuntivi, crea una nuova organizzazione **Free**
   con lo stesso account e selezionala per CompCoach. Supabase permette fino a
   due progetti Free attivi complessivamente; il limite non si moltiplica
   creando altre organizzazioni. Nella stessa organizzazione Pro non si
   possono mescolare progetti Pro e Free.
   Il piano Free può mettere in pausa il database dopo una settimana di
   inattività: controlla il progetto e riattivalo dal dashboard prima della
   gara, se necessario. Se invece usi un'organizzazione Pro, controlla il
   costo aggiuntivo indicato prima di creare il progetto; il compute **Micro**
   è sufficiente come punto di partenza per i test.
   Disattiva **Automatically expose new tables** e **Enable Data API**:
   CompCoach usa una connessione PostgreSQL sul server, senza gli endpoint
   REST/GraphQL della Data API. L'opzione **Enable automatic RLS** per le nuove
   tabelle `public` può restare deselezionata: i dati operativi dell'app sono
   nello schema privato `compcoach`.
3. Imposta e conserva la password del database. Attendi che il progetto sia
   pronto.
4. Apri **Connect** e copia la stringa di connessione PostgreSQL della modalità
   **Session pooler**. Deve includere l'utente indicato dal progetto e la porta
   **5432**. Inserisci la password secondo le indicazioni del pannello; se la
   stringa contiene `<YOUR-PASSWORD>`, quel segnaposto va sostituito. Non
   ricostruire a mano l'host. Mantieni `sslmode=require` nella stringa.
5. Nelle impostazioni API recupera **Project URL** e una chiave server privata:
   la nuova **Secret key** (`sb_secret_...`) oppure la precedente
   **service_role**. La chiave pubblica `anon`/publishable non è quella richiesta
   per questo backend.
6. In **Storage → New bucket** crea **compcoach-assets**. Lascia **Public
   bucket disattivato**. Non aggiungere regole di accesso pubblico.

L'app crea le proprie tabelle in uno schema PostgreSQL chiamato `compcoach`.
Non occorre eseguire SQL a mano o abilitare quello schema nella Data API. La
connessione e le chiavi rimangono sul server: i coach continuano ad aprire il
proprio link, scegliere il nome e usare l'app senza un account Supabase.

## 3. Prepara i Secrets e salva il lavoro su GitHub

La struttura da mantenere nel repository è:

| File nella radice del repository | Scopo |
| --- | --- |
| `.streamlit/config.toml` | Aspetto e configurazione Streamlit |
| `.gitignore` | Esclude credenziali e dati runtime da GitHub |
| `packages.txt` | Libreria di sistema per l'OCR |
| `requirements.txt` | Rimanda alle dipendenze dell'app |
| `compcoach_live/app.py` | File di avvio, nella sottocartella attuale |
| `compcoach_live/requirements.txt` | Elenco delle dipendenze Python |

Dalla radice del repository esegui una volta:

```bash
python compcoach_live/prepare_deployment.py
```

Lo script usa i modelli in `compcoach_live/deployment/repository_root`. Crea
configurazione, esempio Secrets e requirements nella radice solo se mancano;
integra `.gitignore` e conserva i pacchetti già presenti in `packages.txt`.
Da v0.10.2 rimuove i commenti da `packages.txt`, compresa l'intestazione aggiunta
dalle versioni precedenti: l'hosting richiede solo nomi di pacchetti, uno per
riga. Non crea né legge un file Secrets reale. Se conserva un `requirements.txt` personale,
verifica che includa anche `-r compcoach_live/requirements.txt`. Non spostare
`app.py` fuori dalla sottocartella.

Per il collegamento dal Codespace crea un file **privato**
`.streamlit/secrets.toml` nella radice del repository, usando come modello
`.streamlit/secrets.example.toml` appena creato. Se preferisci lanciare da dentro
`compcoach_live`, metti i Secrets nella sua `.streamlit` oppure lancia dalla
radice. Streamlit legge i Secrets della cartella da cui viene avviato.

Inserisci questi valori reali nel file privato:

| Voce | Cosa inserire |
| --- | --- |
| `COMPCOACH_REQUIRE_CLOUD` | `true`, così una configurazione incompleta non usa SQLite in silenzio |
| `COMPCOACH_DATABASE_URL` | La stringa **Session pooler** copiata da Connect |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SECRET_KEY` oppure `SUPABASE_SERVICE_ROLE_KEY` | Una sola chiave server privata, nel campo corrispondente |
| `COMPCOACH_STORAGE_BUCKET` | `compcoach-assets` |
| `COMPCOACH_ADMIN_PIN` | Il PIN privato scelto da te |
| `COMPCOACH_PUBLIC_URL` | Il link finale dell'app; aggiornalo quando lo conosci |

Prima del commit, nella sezione **Source Control** di VS Code controlla i file:
devono comparire codice e configurazioni modello, **mai** `secrets.toml`,
database `.db`, backup o immagini della gara. Poi usa **Commit** e **Sync
Changes**. Il solo Codespace non pubblica i cambiamenti su GitHub.

## 4. Scegli se conservare i test già inseriti

Puoi iniziare con un database cloud vuoto: non eseguire la migrazione, configura
l'app cloud e crea una nuova competizione. Conserva comunque il vecchio SQLite
se vuoi poter consultare i test.

Se vuoi trasferire dati e storico esistenti, **non aprire ancora l'app con i
Secrets cloud**: il database di destinazione deve essere vuoto. Ferma prima
CompCoach con **Ctrl+C** nel suo terminale e lascia ferme le altre sessioni di
CompCoach fino al passaggio al nuovo link. Non serve fermare la FencingAPP.

Dalla radice del repository verifica il database locale e crea un backup
coerente (scegli un nome nuovo se il file esiste):

```bash
python compcoach_live/migrate_to_supabase.py --sqlite compcoach_live/data/compcoach.db --backup-file compcoach_live/backups/pre_supabase.db --dry-run
```

Il controllo legge una copia temporanea coerente, valida i dati e le immagini
referenziate e mostra soltanto i conteggi. Il database originale resta intatto.
Se il tuo database è altrove, sostituisci il percorso dopo `--sqlite`; se le
immagini sono altrove, aggiungi `--assets-dir PERCORSO_CARTELLA_ASSETS`.

Quando il controllo riesce, importa nel progetto Supabase **vuoto**:

```bash
python compcoach_live/migrate_to_supabase.py --sqlite compcoach_live/data/compcoach.db --secrets-file .streamlit/secrets.toml --apply
```

Questa procedura conserva gli ID, i token dei link, risultati, giornate e storico
assegnazioni. Tutte le righe vengono confermate insieme. Logo e mappa vengono
caricati nel bucket privato prima della conferma dei dati; un errore blocca
l'importazione e tenta di rimuovere soltanto le immagini create da quel
tentativo. Nessun file remoto preesistente viene sovrascritto. Se la pulizia di
Storage fallisce, viene segnalato: controlla quel bucket prima di riprovare.

La migrazione non unisce due database e non cancella dati: se il progetto cloud
contiene già una competizione, anche di prova, si interrompe. Conserva il backup
e non ripetere `--apply` dopo un'importazione completata.

Se la connessione cade proprio durante la conferma finale, l'esito può essere
incerto: la procedura conserva le immagini caricate e chiede di controllare il
database prima di riprovare. Questo evita di eliminare una mappa già referenziata
da dati che il server potrebbe aver salvato.

## 5. Pubblica l'app su Streamlit Community Cloud

1. Apri [Streamlit Community Cloud](https://share.streamlit.io/) e collega il
   tuo account GitHub.
2. Scegli **Create app / Deploy an app** e il repository CompCoach, con il
   branch che contiene il codice aggiornato.
3. Nel campo **Main file path** scrivi **compcoach_live/app.py**.
4. Scegli il sottodominio del link, se disponibile.
5. In **Advanced settings** imposta Python **3.12** e incolla il contenuto
   del file privato `secrets.toml` nei **Secrets** dell'hosting. Per
   `COMPCOACH_PUBLIC_URL` usa il link finale, per esempio
   `https://nome-scelto.streamlit.app`.
6. Avvia il deploy. Se il link scelto cambia, aggiorna anche
   `COMPCOACH_PUBLIC_URL` nei Secrets e riavvia l'app.

### Accesso dei coach senza login

In Streamlit Community Cloud apri **App settings → Sharing** e verifica che
**Who can view this app** sia impostato su **This app is public and searchable**.
Un'app pubblica si apre dal link senza account Streamlit o GitHub, anche se il
repository del codice è privato. La procedura è descritta nella
[guida ufficiale alla condivisione](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app).

Dentro CompCoach condividi **Share → Coach link**, oppure il link dedicato
alla pratica. In gara il coach seleziona il proprio nome; nella pratica lo
scrive e preme **Start my practice**. Non deve inserire PIN o creare account.
Il PIN nella pagina iniziale riguarda l'amministratore: il solo indirizzo
principale dell'app non è il link operativo per i coach.

Per verificare l'intero percorso, apri il Coach link in una finestra privata
del browser o su un telefono senza sessioni Streamlit/GitHub.

Le dipendenze Python vengono installate dal repository; `packages.txt` fornisce
la libreria di sistema usata dall'OCR. Se il deploy segnala una dipendenza
mancante, conserva il messaggio di errore senza includere i Secrets.

Questo è il primo hosting proposto per i test completi. Prima di usarlo in gara
verifica che disponibilità, eventuale sospensione per inattività e risorse del
piano siano adeguate; avere Supabase non rende automaticamente l'hosting sempre
attivo. Se serve disponibilità continua, il medesimo backend può essere usato
su un server Streamlit sempre acceso senza cambiare i dati Supabase.

Anche Supabase ha una regola da considerare: il piano Free può sospendere
progetti con poca attività in un periodo di sette giorni. Il progetto si può
ripristinare dal dashboard; il piano Pro esclude la sospensione per inattività.
Prima della partenza controlla quindi che siano attivi sia l'hosting sia il
progetto database. Questa regola è riportata nella
[checklist di produzione ufficiale](https://supabase.com/docs/guides/deployment/going-into-prod).

## 6. Verifica da due telefoni prima della gara

- Apri il link Admin, genera di nuovo i link Coach/Coordinator e mandali al
  gruppo di prova. I token migrati restano validi sul nuovo dominio, ma i vecchi
  messaggi contengono ancora il dominio di Codespaces e vanno sostituiti.
- Crea o apri una giornata, assegna un coach e verifica l'aggiornamento sull'altro
  telefono. Controlla il tuo **My Group** anche usando il link Admin.
- Prova una chiamata **On Deck**, la copertura da due coach contemporaneamente,
  l'aiuto globale, availability e un risultato reversibile.
- Chiudi la giornata senza prepararne un'altra, poi concludi la competizione:
  verifica la schermata neutra e che nessun telefono riproponga vecchi atleti.
- Prepara una giornata futura, attivala e verifica il comportamento di un
  vecchio link della stessa competizione.
- Riavvia l'hosting e riapri il link: dati, storico, logo e mappa devono restare
  presenti. Prova anche l'importazione di uno screenshot e la mappa dal telefono.
- Chiudi il Codespace e continua il test dal link pubblico.

Supabase non aggiunge automaticamente notifiche sul telefono bloccato. Per ora
restano gli avvisi dentro l'app e WhatsApp; le notifiche push richiedono un
intervento separato.

## Come tornare ai test locali

Ferma l'app e rimuovi i Secrets cloud dalla configurazione locale (compreso
`COMPCOACH_REQUIRE_CLOUD`, oppure impostalo a `false`). Senza
`COMPCOACH_DATABASE_URL` l'app usa il vecchio SQLite. Non esiste una sincronizzazione
automatica tra il vecchio file e Supabase: modificare entrambi crea due storici
diversi. Durante la gara tutti devono usare il medesimo link cloud.

## Riferimenti ufficiali

- [Connessione PostgreSQL e pooler Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Chiavi API Supabase](https://supabase.com/docs/guides/api/api-keys)
- [Bucket Supabase](https://supabase.com/docs/guides/storage/buckets/creating-buckets)
- [Deploy Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Struttura dei file nell'hosting Streamlit](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)
- [Secrets Streamlit](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Disponibilità dei progetti Supabase](https://supabase.com/docs/guides/deployment/going-into-prod)
