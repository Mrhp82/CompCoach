# CompCoach — esercitazione autonoma

L'amministratore attiva il link una volta, lo condivide e può disinteressarsi
dell'esercitazione. Non deve interpretare il coordinatore, generare chiamate o
far avanzare i coach.

## Attivazione

1. Apri il tuo link Admin.
2. Vai in **Setup → Training**. Se sei sulla Home, apri **Practice · coach training**.
3. Scegli i coach invitati. Per aggiungerne uno, scrivi il nome nella tendina **Coaches taking part** e seleziona **Add**. Seleziona **7 days** o **14 days**.
4. Premi **Activate autonomous practice**.
5. Copia il **Coach practice link** nella sezione **Share** mostrata nella pagina e invialo ai coach. È distinto dal link della gara reale.

Puoi tornare alla competizione con **Back to competition** o chiudere la
pagina. **Participation**, nella gestione della pratica, mostra chi è entrato
e il punto raggiunto. È una consultazione facoltativa: il percorso non dipende
dal tuo collegamento. **End practice access** interrompe anticipatamente
l'accesso alla pratica; non modifica la gara reale.

## Cosa fa ogni coach

Il coach apre il link e sceglie il proprio nome. L'app apre automaticamente il
suo percorso personale con atleti fittizi, colleghi virtuali e un coordinatore
simulato. La scritta **TRAINING · Practice only** distingue l'esercitazione.

Un compito alla volta indica cosa fare usando i veri pulsanti di **My Group**
e **Live**. **Need a hint?** aiuta quando il comando non è evidente. Non ci sono
pulsanti fittizi per simulare vittorie o coperture: le azioni si salvano e
modificano gli stessi dati operativi usati in gara, all'interno della pratica.

Il percorso passa dalla lettura degli assegnamenti ai gironi, ai risultati,
alla disponibilità, poi alle dirette per pod, alle chiamate e alla copertura.
Comprende richieste d'aiuto, prese in carico, risultati Won/Lost, Bye e un
assalto AFM contro AFM, fino alla chiusura della giornata fittizia. L'app
prepara autonomamente le importazioni, gli assegnamenti e i semafori di fase.

Esempi di situazioni simulate:

- Il coach si segna con un atleta: un altro atleta di cui è responsabile viene chiamato. Dopo circa 15 secondi può intervenire un collega virtuale; in altre ripetizioni nessuno è libero e l'avviso resta aperto.
- Il risultato libera il coach: dopo una breve attesa arriva una chiamata improvvisa Now, On deck oppure In the hole per un atleta con coach impegnato. Il coach può premere **I'll take over**, poi registrare la presenza fisica in pedana.
- Il coordinatore simulato assegna un altro atleta al coach rimasto libero. Il nuovo incarico compare in **My Group**, anche quando riguarda un altro evento.
- Una richiesta d'aiuto riceve una risposta virtuale; la chiamata successiva può avere una pedana diversa dalla precedente.

Ogni coach può entrare in un momento diverso. Chi arriva dopo comincia
dall'inizio. Chi riapre il link riprende il proprio percorso. Al termine,
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

**Il servizio che ospita l'app deve restare acceso e raggiungibile.** Il
periodo di 7/14 giorni riguarda l'accesso, non avvia o mantiene sveglio un
Codespace. Il salvataggio in Supabase, da solo, non ospita l'interfaccia.

## Installazione dell'aggiornamento nel Codespace

Carica `CompCoach_Live_v0.10.2.zip` in `/workspaces/CompCoach`.
Ferma l'app precedente con Ctrl+C, poi esegui:

```bash
cd /workspaces/CompCoach
unzip -o CompCoach_Live_v0.10.2.zip
cd compcoach_live
bash launch.sh
```

L'aggiornamento conserva la sottocartella `compcoach_live`. Il pacchetto non
contiene database o credenziali: i tuoi dati non vengono sostituiti. Le nuove
tabelle vengono aggiunte all'avvio, anche sul backend PostgreSQL configurato.
