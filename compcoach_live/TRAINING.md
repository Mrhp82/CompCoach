# CompCoach — esercitazione autonoma

L'amministratore attiva il link una volta, lo condivide e può disinteressarsi
dell'esercitazione. Non deve interpretare il coordinatore, generare chiamate o
far avanzare i coach.

## Attivazione

1. Apri il tuo link Admin.
2. Vai in **Setup → Training**. Se sei sulla Home, apri **Practice · coach training**.
3. Seleziona **7 days** o **14 days**. Non serve preparare una lista di coach: ognuno inserisce il proprio nome quando entra.
4. Premi **Activate autonomous practice**.
5. Copia il **Coach practice link** nella sezione **Share** mostrata nella pagina e invialo ai coach. È distinto dal link della gara reale.

Puoi tornare alla competizione con **Back to competition** o chiudere la
pagina. **Participation**, nella gestione della pratica, mostra chi è entrato
e il punto raggiunto. È una consultazione facoltativa: il percorso non dipende
dal tuo collegamento. **End practice access** interrompe anticipatamente
l'accesso alla pratica; non modifica la gara reale.

## Cosa fa ogni coach

Il coach apre il link comune, scrive il proprio nome in **Your name** e preme
**Start my practice**. L'app apre un percorso personale nuovo con atleti
fittizi, colleghi virtuali e un coordinatore simulato. Due persone che
inseriscono lo stesso nome hanno comunque percorsi e progressi separati.
Il titolo della pratica e la guida 🧪 distinguono l'esercitazione dalla gara.

Una guida compatta resta fissa in alto anche scorrendo la pagina e indica il
passaggio corrente e la prossima azione, usando i veri pulsanti di **My Group**
e **Team situation**. **Need a hint?** contiene istruzioni complete, scenario, riscontro
sulle azioni e scadenza dell'accesso. Non ci sono
pulsanti fittizi per simulare vittorie o coperture: le azioni si salvano e
modificano gli stessi dati operativi usati in gara, all'interno della pratica.

Il percorso passa dalla lettura degli assegnamenti ai gironi, ai risultati,
alla disponibilità, poi alle dirette per pod, alle chiamate e alla copertura.
Comprende richieste d'aiuto, prese in carico, risultati Won/Lost, Bye e un
assalto AFM contro AFM, fino alla chiusura della giornata fittizia. L'app
prepara autonomamente le importazioni, gli assegnamenti e i semafori di fase.

Gli assegnamenti ordinari ai gironi e ai pod sono impliciti: non devi
accettare ogni atleta. **My Group** mostra il lavoro assegnato;
**Team situation** mostra la situazione condivisa, i coach disponibili e le
richieste urgenti. Il nuovo nome sostituisce l'etichetta **Live** della
pagina condivisa; i collegamenti già salvati continuano a funzionare.

Esempi di situazioni simulate:

- Il coach si segna con un atleta: un altro atleta di cui è responsabile viene chiamato. Dopo circa 15 secondi può intervenire un collega virtuale; in altre ripetizioni nessuno è libero e l'avviso resta aperto.
- Il risultato libera il coach: dopo una breve attesa arriva una chiamata improvvisa Now, On deck oppure In the hole per un atleta con coach impegnato. Il coach può premere **I'll take over**, poi registrare la presenza fisica in pedana.
- Il coordinatore simulato invia una richiesta di copertura per un assalto improvviso a più coach disponibili, compreso quello che si esercita. Chi preme per primo **I'll cover this bout** prende temporaneamente quell'atleta; la richiesta si chiude per gli altri destinatari. L'atleta compare in **My Group**, anche se appartiene a un altro evento, e il coach che accetta non risulta più disponibile. Il piano originale dei coach e dei pod resta invariato. **I’m with [atleta]** registra poi l'arrivo fisico e avvia il timer di occupazione.
- Una richiesta d'aiuto riceve una risposta virtuale; la chiamata successiva può avere una pedana diversa dalla precedente.

Nei gironi, **Need help** serve per emergenze reali: per esempio diversi
assalti già svolti senza assistenza o un atleta lasciato senza coaching che si
sente abbandonato. Gli altri coach stanno già seguendo i propri gruppi. La
lezione simulata presenta un'emergenza di questo tipo e insegna a rispondere
con **I’m coming**.

In gara, Admin e Coordinatore possono anche inviare **Request coverage** per
un singolo assalto a uno o più coach disponibili. Accettare prenota la
responsabilità; nei gironi, il coach di emergenza conferma il proprio arrivo
con **I’m with [atleta]**. Al completamento del risultato del girone vengono
puliti chiamata, richiesta d'aiuto, presa in carico e copertura. Il coach
ritorna disponibile se non ha altri incarichi live aperti.

Una richiesta di copertura reale scade dopo 15 minuti; Admin/Coordinatore
possono annullarla e inviarne una nuova. Durante la lezione autonoma, se
l'offerta resta senza risposta e scade o viene annullata, l'app prepara una
nuova richiesta con una chiamata aggiornata. Non riprende automaticamente
assalti già accettati, coperti o conclusi. Il coach può così riprendere la
lezione dopo una pausa senza intervento dell'amministratore.

Quando il coach è fisicamente con un atleta, **My Group** mostra subito il
nome, la pedana e il tempo trascorso. Nelle dirette i pulsanti **Won / Lost**
sono nella stessa barra: il risultato si salva con un tap e libera il coach.
Il pulsante **I’m with [atleta]** non compare più quando la presenza è già
registrata. Le chiamate successive hanno priorità sugli atleti non ancora
chiamati. Le vittorie in diretta fanno ruotare la coda; i gironi conclusi e
gli atleti Out restano nelle sezioni dedicate.

Ogni coach può entrare in un momento diverso. Chi arriva dopo comincia
dall'inizio. Conserva l'URL personale che si apre dopo l'ingresso: ricaricandolo
o riaprendolo riprendi quel percorso. Il link comune, invece, permette di
iniziare una pratica separata dopo aver inserito il nome, anche se era già
stato usato. Al termine,
**Practice again** ricomincia il suo esercizio con una nuova variante, senza
azzerare quello degli altri.

Se il coach desidera ricominciare mentre sta ancora imparando, può aprire
**Practice options → Restart my practice**. Un risultato inserito per errore
si può anche sistemare normalmente da **Correct DE results**.

## Dati, scadenza e funzionamento

Le gare reali, i risultati e gli assegnamenti restano separati. Le esercitazioni
non compaiono nello storico ordinario delle competizioni o delle assegnazioni
della stagione. I colleghi virtuali non vengono aggiunti alla lista maestri
reale. La pratica usa lo stesso database configurato per l'app, SQLite o
PostgreSQL/Supabase, e conserva progressi ed eventi programmati.

Il link scade automaticamente dopo il periodo scelto. Anche azioni inviate da
una vecchia schermata vengono rifiutate dopo la scadenza. Per un nuovo periodo
basta attivare una nuova pratica e condividere il nuovo link.

Gli scenari vengono elaborati durante l'uso dell'app e i suoi aggiornamenti
automatici. Non servono un amministratore connesso, un servizio AI a pagamento
o un processo esterno che invia chiamate. Se il telefono va in standby, al
ritorno l'app aggiorna il percorso e gli eventi maturati. Non introduce
notifiche push a telefono bloccato.

Da v0.10.3 un unico controllo leggero verifica i cambiamenti ogni cinque
secondi, sia nella pratica sia nella gara reale. Se nulla cambia, la schermata
non viene ridisegnata. Gli scenari in attesa continuano a scattare al momento
previsto; i tempi trascorsi visibili si aggiornano ogni minuto.

**Il servizio che ospita l'app deve restare acceso e raggiungibile.** Il
periodo di 7/14 giorni riguarda l'accesso, non avvia o mantiene sveglio un
Codespace. Il salvataggio in Supabase, da solo, non ospita l'interfaccia.

## Installazione dell'aggiornamento nel Codespace

Carica `CompCoach_Live_v0.10.5.zip` in `/workspaces/CompCoach`.
Ferma l'app precedente con Ctrl+C, poi esegui:

```bash
cd /workspaces/CompCoach
unzip -o CompCoach_Live_v0.10.5.zip
cd compcoach_live
bash launch.sh
```

L'aggiornamento conserva la sottocartella `compcoach_live`. Il pacchetto non
contiene database o credenziali: i tuoi dati non vengono sostituiti. Le nuove
tabelle vengono aggiunte all'avvio, anche sul backend PostgreSQL configurato.
