#!/usr/bin/env python3
"""
midiplayer.py - Player MIDI da terminale basato su FluidSynth (pyfluidsynth)
=============================================================================

Riproduce file .mid/.midi usando FluidSynth tramite i binding Python
"pyfluidsynth" (chiamate dirette a libfluidsynth, nessun processo esterno
e nessun parsing di comandi testuali). Il file MIDI viene letto una sola
volta con "mido": la stessa sequenza di eventi alimenta sia il motore di
riproduzione (un thread dedicato che invia gli eventi al synth rispettando
i tempi) sia le informazioni mostrate a schermo (note attive, accordi,
testi karaoke).

Requisiti:
    - libreria di sistema libfluidsynth (la libreria, non necessariamente
      il comando a riga di comando "fluidsynth")
        Debian/Ubuntu: sudo apt install libfluidsynth3   (o: fluidsynth)
        Arch:          sudo pacman -S fluidsynth
        macOS:         brew install fluid-synth
    - libreria python "pyfluidsynth" (binding a libfluidsynth)
        pip install pyFluidSynth
    - libreria python "mido" per leggere/interpretare i file MIDI
        pip install mido
    - un SoundFont (.sf2), es. FluidR3_GM.sf2

Uso:
    python3 midiplayer.py
        (all'avvio chiede il percorso del SoundFont e la cartella/file MIDI;
         lasciando il campo vuoto viene riusato l'ultimo valore indicato,
         salvato in ~/.config/midiplayer/config.json)

    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 brano1.mid brano2.mid
    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 --dir ./cartella_midi
    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 --dir ./midi --audio-driver pulseaudio

    Esportazione offline in WAV (nessuna interfaccia, rendering più veloce
    del tempo reale perché non passa dalla scheda audio):
    python3 midiplayer.py --soundfont FluidR3_GM.sf2 brano.mid --export brano.wav
    python3 midiplayer.py --soundfont FluidR3_GM.sf2 --dir ./midi --export ./wav_out/

Comandi da tastiera:
    SPAZIO      Pausa / Riprendi
    <- / ->     Indietro / Avanti di 5 secondi nel brano corrente
    N           Salta al brano successivo
    B           Torna al brano precedente
    R           Torna all'inizio del brano corrente
    SU / GIU    Naviga la playlist (selezione, con scorrimento automatico)
    INVIO       Riproduce il brano selezionato nella playlist
    + / -       Alza / abbassa il volume (PagSu/PagGiu equivalenti)
    < / >       Rallenta / velocizza la riproduzione
    [ / ]       Trasponi giù / su di un semitono (fino a +-24)
    L           Cambia modalità di ripetizione (Off -> Tutti -> Singolo)
    S           Attiva/disattiva la riproduzione casuale (Shuffle)
    1-9, 0      Silenzia/riattiva il canale MIDI 1-10 (0 = canale 10,
                di solito le percussioni) - utile come base per esercitarsi
    F1-F6       Silenzia/riattiva i canali MIDI 11-16
    F7          Cambia SoundFont a caldo (chiede il percorso del nuovo .sf2)
    P           Cambia lo strumento (Program Change) di un canale
                (chiede "canale programma", es. "1 41" = canale 1, programma 41)
    ? / H       Mostra/nasconde la legenda comandi (nascosta di default, per
                lasciare più spazio verticale a piano roll e playlist)
    Q           Esci

Durante la riproduzione vengono mostrati anche il nome del file, il BPM e la
tonalità correnti, le note attualmente in esecuzione (canale percussioni
escluso), l'accordo riconosciuto (con eventuale rivolto, es. "Do maj / Mi"),
una piano-roll ASCII a blocchi con le note in arrivo colorate per canale
MIDI, il titolo e l'autore/copyright letti dai metadati del file e, quando
presenti, i testi (lyrics) sincronizzati come in un karaoke. Se il terminale
supporta i colori, barra di avanzamento/stato sono verdi/ciano e l'accordo
rilevato è in giallo per risaltare a colpo d'occhio.

Se l'audio gracchia/distorce:
    - aumenta il buffer: --period-size 2048 --periods 6 (o valori più alti)
    - se il tuo sistema usa PipeWire, prova a fissare la frequenza:
      --sample-rate 48000
    - abbassa il volume per evitare clipping: --gain 0.3
    - su macchine lente, disattiva gli effetti: --no-effects
    - prova un driver audio diverso: --audio-driver pulseaudio (o alsa/pipewire)
"""

import argparse
import bisect
import contextlib
import curses
import json
import os
import random
import re
import shlex
import sys
import threading
import time
import wave

try:
    import mido
except ImportError:
    print("Questo programma richiede la libreria 'mido'.\nInstallala con: pip install mido")
    sys.exit(1)

try:
    import fluidsynth
except ImportError:
    print(
        "Questo programma richiede la libreria 'pyfluidsynth' (i binding Python di FluidSynth).\n"
        "Installala con: pip install pyFluidSynth\n\n"
        "Serve anche la libreria di sistema libfluidsynth (non il comando a riga di comando):\n"
        "    Debian/Ubuntu: sudo apt install libfluidsynth3   (o: fluidsynth)\n"
        "    Arch:          sudo pacman -S fluidsynth\n"
        "    macOS:         brew install fluid-synth\n"
    )
    sys.exit(1)


DEFAULT_SOUNDFONT_PATHS = [
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/soundfonts/default.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
]

SEEK_STEP = 5.0  # secondi per un singolo "avanti"/"indietro"

GAIN_STEP = 0.1
GAIN_MIN = 0.0
GAIN_MAX = 2.0

SPEED_STEP = 0.05
SPEED_MIN = 0.25
SPEED_MAX = 3.0

TRANSPOSE_STEP = 1
TRANSPOSE_MIN = -24
TRANSPOSE_MAX = 24

PIANO_ROLL_ROWS = 6
PIANO_ROLL_LOOKAHEAD = 2.5  # secondi di "anticipo" mostrati nella piano roll
PIANO_ROLL_START_OCTAVE = 1  # ottava più bassa mostrata (1 = Do1/nota MIDI 24)
PIANO_ROLL_NUM_OCTAVES = 6   # quante ottave mostrare: modifica questo valore per ampliare/restringere la piano roll

CONFIG_PATH = os.path.expanduser("~/.config/midiplayer/config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass  # il salvataggio della configurazione non è critico


def prompt_with_default(message, default):
    """Chiede un valore all'utente; se lascia vuoto, riusa 'default'."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"{message}{suffix}: ").strip()
    return answer if answer else (default or "")


def find_default_soundfont():
    for p in DEFAULT_SOUNDFONT_PATHS:
        if os.path.isfile(p):
            return p
    return None


def format_time(seconds):
    if seconds < 0:
        seconds = 0
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


NOTE_NAMES = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]

# Canale MIDI 10 (indice 9) è convenzionalmente riservato alla batteria/percussioni:
# i "note number" lì indicano lo strumento percosso, non un'altezza musicale,
# quindi lo escludiamo dal riconoscimento di note/accordi e dalla piano roll.
PERCUSSION_CHANNEL = 9

CHORD_TEMPLATES = [
    ("maj7", (0, 4, 7, 11)),
    ("7", (0, 4, 7, 10)),
    ("min7", (0, 3, 7, 10)),
    ("dim7", (0, 3, 6, 9)),
    ("m7b5", (0, 3, 6, 10)),
    ("maj9", (0, 2, 4, 7, 11)),
    ("9", (0, 2, 4, 7, 10)),
    ("min9", (0, 2, 3, 7, 10)),
    ("6", (0, 4, 7, 9)),
    ("min6", (0, 3, 7, 9)),
    ("add9", (0, 2, 4, 7)),
    ("maj", (0, 4, 7)),
    ("min", (0, 3, 7)),
    ("dim", (0, 3, 6)),
    ("aug", (0, 4, 8)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
]

# Peso di ogni intervallo (in semitoni dalla fondamentale) nel punteggio di
# riconoscimento. Fondamentale e terza (maggiore/minore) sono le note che
# definiscono l'identità dell'accordo: se mancano, il match viene scartato.
# Settima e sesta sono "importanti" (definiscono l'estensione). La quinta
# giusta è la nota più facilmente omessa in pratica (specialmente su
# synth/tastiere, dove non aggiunge colore) e quindi ha il peso più basso:
# la sua assenza costa poco e non deve far perdere all'accordo giusto contro
# un template più povero. Le altre "quinte" caratteristiche (diminuita,
# eccedente) e la quarta dei sus hanno peso medio, perché sono la nota che
# distingue quei template da un semplice maj/min.
INTERVAL_WEIGHT = {
    0: 3,   # fondamentale
    1: 2,   # 9a minore
    2: 2,   # 9a
    3: 3,   # 3a minore
    4: 3,   # 3a maggiore
    5: 2,   # 4a giusta (sus4)
    6: 2,   # 5a diminuita (dim/m7b5)
    7: 1,   # 5a giusta -> peso basso, è la nota più spesso omessa
    8: 2,   # 5a eccedente (aug)
    9: 2,   # 6a/13a
    10: 2,  # 7a minore
    11: 2,  # 7a maggiore
}

# Note "extra" rispetto al template di base: invece di penalizzarle sempre
# allo stesso modo, se corrispondono a una tensione armonica riconoscibile
# (9a, 11a, 13a o loro alterazioni) vengono riportate nel nome dell'accordo
# invece di essere ignorate/penalizzate, es. "La 7 (b13)" invece di "La 7".
EXTENSION_LABELS = {1: "b9", 2: "9", 3: "#9", 5: "11", 6: "#11", 8: "b13", 9: "13"}
MAX_EXTENSIONS_SHOWN = 2  # oltre questa soglia l'accordo è troppo "sporco": non etichettare

CHORD_MISSING_PENALTY_MULT = 1.5  # amplifica il costo dei toni mancanti in proporzione al loro peso
CHORD_EXTRA_PENALTY = 1           # penalità per ogni nota extra SENZA un'etichetta di tensione nota
CHORD_MIN_COVERAGE = 0.45         # copertura minima (peso presente / peso totale template) per accettare

# Spelling "a bemolle" per le fondamentali dei tasti neri dove è la grafia di
# gran lunga più comune nei chart reali (Reb, Mib, Lab, Sib). Fa#/Solb resta
# col diesis perché "Fa#" è la spellatura standard, specie per dim/m7b5.
FLAT_ROOT_SPELLING = {1: "Reb", 3: "Mib", 8: "Lab", 10: "Sib"}


def spell_root(pitch_class):
    return FLAT_ROOT_SPELLING.get(pitch_class, NOTE_NAMES[pitch_class])


# mido esprime la tonalità (meta-evento "key_signature") con la notazione
# anglosassone, es. "C", "F#", "Bbm": lettera base + eventuale alterazione +
# eventuale "m" finale per il modo minore.
KEY_LETTER_TO_ITALIAN = {"C": "Do", "D": "Re", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}
KEY_SIGNATURE_RE = re.compile(r"^([A-G])([#b]?)(m?)$")


def format_key_signature(key):
    """Converte una tonalità in notazione anglosassone (es. 'Bbm') nel
    formato italiano usato nel resto del player (es. 'Sib min')."""
    if not key:
        return None
    match = KEY_SIGNATURE_RE.match(key)
    if not match:
        return key  # formato inatteso: meglio mostrare il valore grezzo che nulla
    letter, accidental, minor = match.groups()
    name = KEY_LETTER_TO_ITALIAN.get(letter, letter) + accidental
    name += " min" if minor else " magg"
    return name


def sanitize_text(text):
    """Ripulisce una stringa proveniente dai metadati di un file MIDI.

    Alcuni file MIDI contengono byte null o altri caratteri di controllo
    dentro ai meta-eventi (track_name, copyright, lyrics, text): passarli
    così com'è a curses.addstr() causa un crash ("embedded null character").
    """
    if not text:
        return text
    cleaned = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return cleaned.strip()


def note_name(midi_note):
    name = NOTE_NAMES[midi_note % 12]
    octave = midi_note // 12 - 1
    return f"{name}{octave}"


def guess_chord(notes):
    """Prova a riconoscere un accordo comune dato un insieme di note MIDI attive.

    A differenza di una semplice riduzione a classi di altezza (0-11), qui si
    riceve l'elenco delle note "vere" per poter individuare anche la nota al
    basso: se il basso non coincide con la fondamentale dell'accordo
    riconosciuto, il risultato viene mostrato come rivolto, es. "Do maj / Mi".

    Il matching è a punteggio pesato (non a inclusione esatta): ogni
    intervallo del template pesa in base a quanto è "identificativo"
    dell'accordo (fondamentale/terza pesano molto, la quinta giusta pesa
    poco perché è la prima nota che viene omessa nella pratica reale), e:
      - le note del template presenti vengono premiate in base al loro peso;
      - le note del template MANCANTI vengono penalizzate in proporzione al
        loro peso (mancare la terza costa molto più che mancare la quinta);
        se manca la fondamentale o la terza il match viene scartato del
        tutto, perché senza quelle l'identità dell'accordo non è definita;
      - le note suonate ma estranee al template vengono trattate in due modi
        diversi: se corrispondono a una tensione armonica riconoscibile (9a,
        11a, 13a o loro alterazioni) vengono riportate nel nome invece di
        essere ignorate (es. "La 7 (b13)"); se invece non corrispondono a
        nulla di riconoscibile (probabile nota di passaggio/melodia) ricevono
        solo una lieve penalità, per non far scartare l'accordo giusto.
    Vince il template con lo score più alto; a parità si preferisce quello
    più esteso (specifico) e, a ulteriore parità, quello la cui fondamentale
    coincide con la nota al basso (ipotesi più probabile in stato fondamentale).

    Infine, alla fondamentale e all'eventuale nota al basso viene applicata
    una spellatura "a bemolle" per i tasti neri dove è la grafia standard nei
    chart reali (es. la radice sul semitono 10 diventa "Sib", non "La#").
    """
    notes = sorted(set(notes))
    if len(notes) < 2:
        return None

    pitch_classes = set(n % 12 for n in notes)
    if len(pitch_classes) < 3:
        # con meno di 3 classi di altezza distinte (es. un semplice intervallo
        # di quinta, o solo ottave della stessa nota) non c'è abbastanza
        # materiale per parlare di un vero accordo: meglio non azzardare
        return None
    bass_pitch_class = min(notes) % 12

    best = None  # (score, dimensione_template, nome, root, estensioni_etichettate)
    for root in pitch_classes:
        intervals_present = set((pc - root) % 12 for pc in pitch_classes)
        for name, template in CHORD_TEMPLATES:
            template_set = set(template)
            matched = template_set & intervals_present
            missing = template_set - intervals_present
            extra = intervals_present - template_set

            # fondamentale o terza assenti -> il template non è credibile,
            # indipendentemente da quanto "coprono" bene le altre note
            if any(INTERVAL_WEIGHT[i] >= 3 for i in missing):
                continue

            template_weight = sum(INTERVAL_WEIGHT[i] for i in template_set)
            matched_weight = sum(INTERVAL_WEIGHT[i] for i in matched)
            missing_weight = sum(INTERVAL_WEIGHT[i] for i in missing)
            coverage = matched_weight / template_weight if template_weight else 0
            if coverage < CHORD_MIN_COVERAGE:
                continue

            labeled_extras = sorted(i for i in extra if i in EXTENSION_LABELS)
            unlabeled_extras = [i for i in extra if i not in EXTENSION_LABELS]

            score = (
                matched_weight
                - CHORD_MISSING_PENALTY_MULT * missing_weight
                - CHORD_EXTRA_PENALTY * len(unlabeled_extras)
            )

            candidate = (score, len(template_set), name, root, tuple(labeled_extras))
            if best is None or candidate[:2] > best[:2] or (
                candidate[:2] == best[:2] and root == bass_pitch_class and best[3] != bass_pitch_class
            ):
                best = candidate

    if best is None:
        return None

    _, _, name, root, labeled_extras = best
    chord_name = f"{spell_root(root)} {name}"
    if labeled_extras and len(labeled_extras) <= MAX_EXTENSIONS_SHOWN:
        chord_name += f" ({','.join(EXTENSION_LABELS[i] for i in labeled_extras)})"
    if bass_pitch_class != root:
        chord_name += f" / {spell_root(bass_pitch_class)}"
    return chord_name


# Tipi di messaggio "channel voice" che ci interessano per la riproduzione:
# sono gli unici che il synth deve effettivamente ricevere.
PLAYABLE_TYPES = ("note_on", "note_off", "control_change", "program_change", "pitchwheel")


@contextlib.contextmanager
def _suppress_native_output():
    """Silenzia temporaneamente stderr a livello di file descriptor.

    FluidSynth/GLib possono stampare avvisi (es. "Instrument not found...",
    "SDL3 not initialized", "GLib-GObject-CRITICAL") scrivendo direttamente
    sul file descriptor 2 (stderr) di sistema, bypassando sys.stdout/stderr
    di Python: redirect_stdout/redirect_stderr non basterebbero a
    nasconderli. Qui invece si duplica temporaneamente SOLO il file
    descriptor 2 su /dev/null e lo si ripristina subito dopo.

    Va toccato solo stderr e MAI stdout (fd 1): quando questa funzione
    avvolge l'intera sessione curses, curses.wrapper() ha bisogno che fd 1
    sia ancora il terminale vero per inizializzare lo schermo (cbreak()/
    nocbreak() falliscono con "returned ERR" se fd 1 punta a /dev/null).
    """
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stderr_fd = os.dup(2)
    except OSError:
        # su piattaforme dove non è possibile duplicare i file descriptor
        # (raro), meglio mostrare i warning che far fallire l'avvio
        yield
        return
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_stderr_fd, 2)
        os.close(devnull_fd)
        os.close(saved_stderr_fd)


def analyze_midi(path):
    """Analizza un file MIDI ed estrae tutte le informazioni utili al player.

    Un'unica lettura con "mido" alimenta sia la UI sia il motore di
    riproduzione, eliminando la doppia analisi che serviva quando la
    riproduzione vera e propria era delegata al player interno di
    FluidSynth via CLI.

    Ritorna un dizionario con:
        duration        durata totale in secondi
        note_events     lista ordinata di (tempo_secondi, 'on'/'off', canale, nota, tick_assoluto)
                         usata dalla UI per note attive/accordi/piano roll; il tick
                         assoluto (posizione grezza nel file, non convertita in
                         secondi) serve alla piano roll per classificare la durata
                         delle note molto brevi (1-2 tick, note "staccatissime")
        events          lista ordinata di (tempo_secondi, msg) con TUTTI i
                         messaggi "channel voice" (note, cc, program change,
                         pitch bend): è la sequenza che il player invia al synth
        title           titolo del brano (dal primo evento 'track_name' utile), o None
        author          autore/copyright (dal primo evento 'copyright'), o None
        lyrics          lista ordinata di (tempo_secondi, testo) per la modalità karaoke
        tempo_events    lista ordinata di (tempo_secondi, bpm) per ogni cambio di tempo
        key_signature   tonalità dichiarata nel file (es. "Do magg", "Re min"), o None
    """
    empty = {
        "duration": 0.0, "note_events": [], "events": [],
        "title": None, "author": None, "lyrics": [],
        "tempo_events": [], "key_signature": None,
    }
    try:
        mid = mido.MidiFile(path)
    except Exception:
        return empty

    duration = mid.length  # mido calcola già tenendo conto dei cambi di tempo

    title = None
    author = None
    key_signature = None
    note_events = []
    events = []
    lyric_events = []   # eventi 'lyrics' veri e propri (karaoke standard)
    text_events = []    # eventi 'text' generici, usati come ripiego
    tempo_events = []   # (tempo_secondi, bpm)
    elapsed = 0.0
    tick_elapsed = 0
    try:
        # Iteriamo in parallelo due viste della STESSA sequenza di messaggi:
        # "mid" (via merge_tracks + conversione tempo-aware) dà il tempo in
        # SECONDI, usato per la riproduzione e per tutta la UI; merge_tracks
        # "grezzo" dà lo stesso identico messaggio, nello stesso ordine, ma
        # con .time in TICK non convertiti. Le due sequenze sono garantite
        # allineate 1:1 (mid.__iter__ è definito internamente proprio come
        # merge_tracks + conversione, quindi rifarlo separatamente riproduce
        # esattamente la stessa sequenza): zippandole otteniamo per ogni
        # messaggio sia l'istante in secondi sia la posizione assoluta in
        # tick, senza dover leggere il file due volte in modo scollegato.
        for raw_msg, msg in zip(mido.merge_tracks(mid.tracks), mid):
            elapsed += msg.time
            tick_elapsed += raw_msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                note_events.append((elapsed, "on", msg.channel, msg.note, tick_elapsed))
                events.append((elapsed, msg))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                note_events.append((elapsed, "off", msg.channel, msg.note, tick_elapsed))
                events.append((elapsed, msg))
            elif msg.type in ("control_change", "program_change", "pitchwheel"):
                events.append((elapsed, msg))
            elif msg.type == "track_name":
                name = sanitize_text(msg.name)
                if name and title is None:
                    title = name
            elif msg.type == "copyright":
                text = sanitize_text(msg.text)
                if text and author is None:
                    author = text
            elif msg.type == "lyrics":
                text = sanitize_text(msg.text)
                if text:
                    lyric_events.append((elapsed, text))
            elif msg.type == "text":
                text = sanitize_text(msg.text)
                if text:
                    text_events.append((elapsed, text))
            elif msg.type == "set_tempo":
                bpm = round(60000000 / msg.tempo) if msg.tempo else 120
                tempo_events.append((elapsed, bpm))
            elif msg.type == "key_signature" and key_signature is None:
                key_signature = format_key_signature(msg.key)
    except Exception:
        note_events = []
        events = []

    # Alcuni file "karaoke" salvano i testi come semplici eventi 'text'
    # invece che 'lyrics': li usiamo solo se non ci sono lyrics vere e proprie.
    lyrics = lyric_events if lyric_events else text_events
    lyrics.sort(key=lambda item: item[0])

    return {
        "duration": duration, "note_events": note_events, "events": events,
        "title": title, "author": author, "lyrics": lyrics,
        "tempo_events": tempo_events, "key_signature": key_signature,
    }


def _build_note_spans(note_events):
    """Accoppia gli eventi 'on'/'off' di note_events in "span" completi:
    (tempo_on_sec, tempo_off_sec, tick_on, tick_off, canale, nota).

    Serve alla piano roll per conoscere la durata di ogni nota (non solo il
    suo istante di attacco) e scegliere di conseguenza la "forma" del
    blocco da disegnare. Le percussioni (PERCUSSION_CHANNEL) sono escluse
    a monte, come per il resto della piano roll/riconoscimento accordi.

    L'accoppiamento è FIFO per (canale, nota): nel raro caso di note
    sovrapposte identiche (stessa altezza, stesso canale, retrigger prima
    del rilascio) il primo 'on' si accoppia al primo 'off' successivo.
    Un eventuale 'on' senza un 'off' corrispondente (file troncato) viene
    scartato piuttosto che rischiare una durata falsata.
    """
    pending = {}  # (channel, note) -> lista di (tempo_on_sec, tick_on) in attesa del rispettivo off
    spans = []
    for t, kind, channel, note, tick in note_events:
        if channel == PERCUSSION_CHANNEL:
            continue
        key = (channel, note)
        if kind == "on":
            pending.setdefault(key, []).append((t, tick))
        else:
            queue = pending.get(key)
            if queue:
                on_t, on_tick = queue.pop(0)
                spans.append((on_t, t, on_tick, tick, channel, note))
    spans.sort(key=lambda s: s[0])
    return spans


class FluidSynthTrack:
    """Pilota un'istanza di FluidSynth (via pyfluidsynth) per UN singolo file MIDI.

    Non c'è più un processo esterno: gli eventi vengono inviati al synth da
    un thread dedicato che rispetta i tempi del brano (accelerabili/
    rallentabili a runtime tramite 'speed'). Operazioni come seek, mute di
    un canale, trasposizione o cambio SoundFont ricostruiscono lo stato del
    synth (programmi, controller, note eventualmente ancora suonanti)
    all'istante corrente tramite _resync_state(), invece di limitarsi a
    "saltare" nella sequenza.
    """

    def __init__(self, soundfont, audio_driver, gain,
                 period_size=1024, periods=4, sample_rate=None, no_effects=False):
        self.soundfont_path = soundfont
        self.audio_driver = audio_driver
        self.gain = gain
        self.period_size = period_size
        self.periods = periods
        self.sample_rate = sample_rate or 44100
        self.no_effects = no_effects

        with _suppress_native_output():
            self.synth = fluidsynth.Synth(gain=gain, samplerate=float(self.sample_rate))
            if period_size:
                self.synth.setting("audio.period-size", period_size)
            if periods:
                self.synth.setting("audio.periods", periods)
            if no_effects:
                self.synth.setting("synth.reverb.active", 0)
                self.synth.setting("synth.chorus.active", 0)
            self.synth.start(driver=audio_driver)
            self.sfid = self.synth.sfload(soundfont)
        for ch in range(16):
            self.synth.program_select(ch, self.sfid, 0, 0)

        self.duration = 0.0
        self.note_events = []
        self.events = []
        self._event_times = []
        self._note_spans = []       # (on_sec, off_sec, on_tick, off_tick, canale, nota)
        self._note_span_on_times = []
        self.title = None
        self.author = None
        self.lyrics = []
        self.tempo_events = []    # (tempo_secondi, bpm)
        self._tempo_times = []
        self.key_signature = None

        self.paused = False
        self.finished = False

        # "Orologio" interno del brano: la posizione (in secondi "di brano")
        # viene ricostruita come song_pos_al_ancoraggio + tempo_reale_trascorso * velocità.
        # Questo permette di tenere sincronizzati barra di progresso, testi e
        # il thread di riproduzione anche quando la velocità cambia a runtime.
        self._song_pos = 0.0
        self._anchor = time.monotonic()
        self.speed = 1.0
        self.transpose = 0

        self.muted_channels = set()

        self._gen = 0            # invalidato ad ogni start/seek/reposition
        self._thread = None
        self._stop_flag = False

    # ---- gestione del thread di riproduzione --------------------------------
    def start(self, path):
        self._stop_thread()

        info = analyze_midi(path)
        self.duration = info["duration"]
        self.note_events = info["note_events"]
        self.events = info["events"]
        self._event_times = [t for t, _ in self.events]
        self._note_spans = _build_note_spans(self.note_events)
        self._note_span_on_times = [s[0] for s in self._note_spans]
        self._pitch_events = [(t, msg.channel) for t, msg in self.events if msg.type == "pitchwheel"]
        self._pitch_event_times = [t for t, _ in self._pitch_events]
        self.title = info["title"]
        self.author = info["author"]
        self.lyrics = info["lyrics"]
        self.tempo_events = info["tempo_events"]
        self._tempo_times = [t for t, _ in self.tempo_events]
        self.key_signature = info["key_signature"]

        self.paused = False
        self.finished = False
        self._song_pos = 0.0
        self._anchor = time.monotonic()

        self._resync_state(0.0, retrigger_notes=False)
        self._spawn_thread(0)

    def _spawn_thread(self, start_idx):
        self._gen += 1
        self._stop_flag = False
        self._thread = threading.Thread(
            target=self._playback_loop, args=(self._gen, start_idx), daemon=True
        )
        self._thread.start()

    def _stop_thread(self):
        self._stop_flag = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def stop_process(self):
        self._stop_thread()
        try:
            self.synth.delete()
        except Exception:
            pass

    def _playback_loop(self, gen, start_idx):
        idx = start_idx
        n = len(self.events)
        while not self._stop_flag and gen == self._gen:
            if self.paused:
                time.sleep(0.01)
                continue
            if idx >= n:
                time.sleep(0.05)
                continue
            t_event, msg = self.events[idx]
            now = self.elapsed()
            wait = (t_event - now) / max(self.speed, 0.0001)
            if wait > 0.002:
                time.sleep(min(wait, 0.02))
                continue
            try:
                self._apply_message(msg)
            except Exception:
                pass  # un evento malformato non deve far crashare il player
            idx += 1

    def _apply_message(self, msg):
        ch = msg.channel
        if msg.type == "note_on" and msg.velocity > 0:
            if ch in self.muted_channels:
                return
            note = msg.note + self.transpose
            if 0 <= note <= 127:
                self.synth.noteon(ch, note, msg.velocity)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            note = msg.note + self.transpose
            if 0 <= note <= 127:
                self.synth.noteoff(ch, note)
        elif msg.type == "control_change":
            self.synth.cc(ch, msg.control, msg.value)
        elif msg.type == "program_change":
            self.synth.program_change(ch, msg.program)
        elif msg.type == "pitchwheel":
            # fluid_synth_pitch_bend vuole un valore 0..16383 centrato su 8192,
            # mentre mido usa -8192..8191 centrato su 0.
            self.synth.pitch_bend(ch, msg.pitch + 8192)

    def _resync_state(self, target_time, retrigger_notes=True):
        """Ricostruisce lo stato del synth (programmi, controller, pitch bend,
        note ancora suonanti) come sarebbe all'istante target_time, rigiocando
        gli eventi del file fino a quel punto. Necessario dopo un seek/mute/
        trasposizione: senza, si perderebbero lo strumento giusto sul canale,
        volume/pan, o le note che dovrebbero essere ancora attive.
        """
        self.synth.system_reset()
        for ch in range(16):
            self.synth.program_select(ch, self.sfid, 0, 0)
            # Il volume di canale (CC7) è uno dei controller che lo spec MIDI
            # esclude esplicitamente da "Reset All Controllers": system_reset()
            # non lo riporta al default. Se non lo fissiamo qui a mano, un
            # canale mutato (cc7=0) e mai toccato da un CC7 nel file resta a
            # volume zero per sempre anche dopo lo smute, perché più sotto
            # verrebbe rigiocato solo ciò che il file imposta esplicitamente.
            self.synth.cc(ch, 7, 100)

        active = {}       # (canale, nota) -> velocity
        last_program = {}  # canale -> programma
        last_cc = {}       # (canale, controller) -> valore
        last_pitch = {}    # canale -> pitch (valore mido, -8192..8191)

        end_idx = bisect.bisect_right(self._event_times, target_time)
        for t, msg in self.events[:end_idx]:
            ch = msg.channel
            if msg.type == "note_on" and msg.velocity > 0:
                active[(ch, msg.note)] = msg.velocity
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                active.pop((ch, msg.note), None)
            elif msg.type == "control_change":
                last_cc[(ch, msg.control)] = msg.value
            elif msg.type == "program_change":
                last_program[ch] = msg.program
            elif msg.type == "pitchwheel":
                last_pitch[ch] = msg.pitch

        for ch, prog in last_program.items():
            self.synth.program_change(ch, prog)
        for (ch, control), value in last_cc.items():
            self.synth.cc(ch, control, value)
        for ch, pitch in last_pitch.items():
            self.synth.pitch_bend(ch, pitch + 8192)
        # i canali mutati restano a volume 0 indipendentemente da cosa dice il file
        for ch in self.muted_channels:
            self.synth.cc(ch, 7, 0)

        if retrigger_notes:
            for (ch, note), vel in active.items():
                if ch in self.muted_channels:
                    continue
                tn = note + self.transpose
                if 0 <= tn <= 127:
                    self.synth.noteon(ch, tn, vel)

    def _reposition(self, target_time):
        """Ferma il thread di riproduzione, ricostruisce lo stato del synth a
        target_time e lo fa ripartire da lì. Usato da seek/restart/mute/
        trasposizione/cambio SoundFont: è l'unico punto che sa come
        riallineare in modo coerente audio e stato interno."""
        self._stop_flag = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

        self._resync_state(target_time, retrigger_notes=not self.paused)
        self._song_pos = target_time
        self._anchor = time.monotonic()

        idx = bisect.bisect_left(self._event_times, target_time)
        self._spawn_thread(idx)

    # ---- controlli di riproduzione ------------------------------------------
    def pause(self):
        if not self.paused:
            self._song_pos = self.elapsed()
            self.paused = True
            for ch in range(16):
                self.synth.cc(ch, 123, 0)  # All Notes Off: silenzia subito ciò che suona

    def resume(self):
        if self.paused:
            self._anchor = time.monotonic()
            self.paused = False

    def toggle_pause(self):
        self.pause() if not self.paused else self.resume()

    def restart(self):
        self._reposition(0.0)

    def seek(self, delta_seconds):
        current = self.elapsed()
        target = max(0.0, min(self.duration, current + delta_seconds))
        if target != current:
            self._reposition(target)

    def elapsed(self):
        if self.paused:
            return self._song_pos
        return self._song_pos + (time.monotonic() - self._anchor) * self.speed

    def is_song_over(self):
        return (not self.paused) and self.duration > 0 and self.elapsed() >= self.duration

    # ---- volume ---------------------------------------------------------------
    def change_gain(self, delta):
        self.gain = max(GAIN_MIN, min(GAIN_MAX, self.gain + delta))
        # 'synth.gain' è una impostazione di FluidSynth modificabile in tempo
        # reale: non serve ricreare il synth né inviare comandi separati.
        self.synth.setting("synth.gain", self.gain)

    # ---- velocità di riproduzione -----------------------------------------------
    def change_speed(self, delta):
        # arrotondiamo per restare sempre sulla stessa "griglia" di valori
        # (altrimenti gli errori di virgola mobile o un valore di clamp non
        # allineato al passo impedirebbero di tornare esattamente a 1.0x)
        new_speed = round(self.speed + delta, 2)
        new_speed = round(max(SPEED_MIN, min(SPEED_MAX, new_speed)), 2)
        if new_speed == self.speed:
            return
        # "congela" la posizione corrente nel brano prima di cambiare velocità,
        # così elapsed() resta coerente: qui non serve toccare il synth, la
        # velocità è solo un fattore usato per calcolare i tempi di attesa.
        self._song_pos = self.elapsed()
        self._anchor = time.monotonic()
        self.speed = new_speed

    # ---- trasposizione ----------------------------------------------------------
    def change_transpose(self, delta):
        new_t = max(TRANSPOSE_MIN, min(TRANSPOSE_MAX, self.transpose + delta))
        if new_t == self.transpose:
            return
        self.transpose = new_t
        # ririgioca lo stato corrente così le note eventualmente già suonanti
        # vengono subito ritrasposte, invece di aspettare le prossime note_on
        self._reposition(self.elapsed())

    # ---- note e accordi --------------------------------------------------------
    def active_notes(self):
        """Ritorna la lista (canale, nota) attive nell'istante corrente, note melodiche escluse le percussioni."""
        t = self.elapsed()
        active = {}
        for evt_time, kind, channel, note, _tick in self.note_events:
            if evt_time > t:
                break
            if channel == PERCUSSION_CHANNEL:
                continue
            key = (channel, note)
            if kind == "on":
                active[key] = True
            else:
                active.pop(key, None)
        return sorted(active.keys(), key=lambda ck: ck[1])

    # ---- tempo (BPM) --------------------------------------------------------------
    def current_bpm(self):
        """BPM in vigore all'istante corrente, seguendo eventuali cambi di
        tempo nel brano (non solo quello iniziale)."""
        if not self.tempo_events:
            return 120  # tempo di default quando il file non dichiara nulla
        idx = bisect.bisect_right(self._tempo_times, self.elapsed()) - 1
        if idx < 0:
            return self.tempo_events[0][1]
        return self.tempo_events[idx][1]

    # ---- lyrics / karaoke --------------------------------------------------------
    def current_lyric(self):
        """Ritorna l'ultima riga di testo con timestamp <= istante corrente, se presente."""
        if not self.lyrics:
            return None
        t = self.elapsed()
        current = None
        for evt_time, text in self.lyrics:
            if evt_time > t:
                break
            current = text
        return current

    # ---- mute/solo dei canali (modalità esercizio) --------------------------------
    def toggle_channel_mute(self, channel):
        """Silenzia/riattiva un canale agendo SOLO sul volume (CC7), senza
        passare da _reposition()/_resync_state(): quella strada faceva un
        system_reset() e ri-attaccava (retrigger) tutte le note attive in
        quell'istante, causando un fastidioso "click"/nota ripetuta ad ogni
        mute o smute. Così invece le note eventualmente già suonanti restano
        esattamente dove sono, semplicemente udibili o no.
        """
        if channel in self.muted_channels:
            self.muted_channels.discard(channel)
            self.synth.cc(channel, 7, self._channel_volume_at(channel, self.elapsed()))
        else:
            self.muted_channels.add(channel)
            self.synth.cc(channel, 7, 0)

    def _channel_volume_at(self, channel, target_time):
        """Ultimo valore di CC7 (volume canale) impostato dal file per
        'channel' fino a target_time, o 100 (default MIDI) se il file non lo
        specifica mai: serve per sapere a quale volume tornare quando si
        smuta un canale, senza dover rigiocare l'intero stato del synth.
        """
        end_idx = bisect.bisect_right(self._event_times, target_time)
        volume = 100
        for t, msg in self.events[:end_idx]:
            if msg.type == "control_change" and msg.channel == channel and msg.control == 7:
                volume = msg.value
        return volume

    # ---- SoundFont e strumenti ---------------------------------------------------
    def change_soundfont(self, path):
        """Carica un nuovo SoundFont a caldo, senza riavviare il programma."""
        if not os.path.isfile(path):
            return False
        with _suppress_native_output():
            new_id = self.synth.sfload(path)
        if new_id == -1:
            return False
        old_id = self.sfid
        self.sfid = new_id
        self.soundfont_path = path
        self._reposition(self.elapsed())
        try:
            self.synth.sfunload(old_id)
        except Exception:
            pass
        return True

    def set_program(self, channel, program):
        """Program Change manuale su un canale (es. per cambiare strumento al volo)."""
        self.synth.program_change(channel, program)


class Playlist:
    def __init__(self, files):
        self.files = files
        self.index = 0
        self.selected = 0
        self.shuffle = False
        self.loop_mode = "off"  # "off", "all" (ripeti tutto), "single" (ripeti singolo)

    @property
    def current(self):
        return self.files[self.index]

    def _random_index(self):
        if len(self.files) <= 1:
            return self.index
        choices = [i for i in range(len(self.files)) if i != self.index]
        return random.choice(choices)

    def next(self):
        if not self.files:
            return False
        if self.shuffle:
            self.index = self._random_index()
            self.selected = self.index
            return True
        if self.index < len(self.files) - 1:
            self.index += 1
            self.selected = self.index
            return True
        if self.loop_mode == "all":
            self.index = 0
            self.selected = 0
            return True
        return False

    def prev(self):
        if not self.files:
            return False
        if self.shuffle:
            self.index = self._random_index()
            self.selected = self.index
            return True
        if self.index > 0:
            self.index -= 1
            self.selected = self.index
            return True
        return False

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        return self.shuffle

    def cycle_loop_mode(self):
        order = ["off", "all", "single"]
        self.loop_mode = order[(order.index(self.loop_mode) + 1) % len(order)]
        return self.loop_mode


LOOP_LABELS = {"off": "Off", "all": "Tutti", "single": "Singolo"}

# --- colori --------------------------------------------------------------------
# Numeri di color pair curses (inizializzati in init_colors(), chiamata una
# sola volta all'avvio della UI): definiti qui come costanti così il resto
# del codice li referenzia per nome invece che per numero magico.
COLOR_PAIR_PROGRESS = 1   # barra di avanzamento e stato di riproduzione (verde/cyan)
COLOR_PAIR_CHORD = 2      # accordo rilevato in tempo reale (giallo/arancione)
COLOR_PAIR_ACTIVE_TRACK = 3  # icona ▶ della traccia in riproduzione nella playlist
COLOR_PAIR_BEND = 4       # nota con pitch bend attivo nella piano roll (si distingue dal colore di canale)

# Piano roll: una tavolozza di colori ciclica assegnata per canale MIDI, così
# note di canali diversi (tipicamente melodia/basso/accompagnamento) si
# distinguono a colpo d'occhio. I numeri di pair usati sono 10, 11, 12, ...
PIANO_ROLL_CHANNEL_PAIR_BASE = 10
PIANO_ROLL_CHANNEL_COLORS = [
    curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_MAGENTA,
    curses.COLOR_BLUE, curses.COLOR_YELLOW, curses.COLOR_WHITE,
]


def init_colors():
    """Inizializza i color pair curses, se il terminale li supporta.
    Ritorna True/False: tutto il resto del codice deve continuare a
    funzionare anche senza colori (terminali monocromatici), quindi ogni
    punto che li usa controlla questo valore prima di applicare un colore.
    """
    if not curses.has_colors():
        return False
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1  # sfondo trasparente/quello di default del terminale
    except curses.error:
        bg = curses.COLOR_BLACK
    try:
        curses.init_pair(COLOR_PAIR_PROGRESS, curses.COLOR_CYAN, bg)
        curses.init_pair(COLOR_PAIR_CHORD, curses.COLOR_YELLOW, bg)
        curses.init_pair(COLOR_PAIR_ACTIVE_TRACK, curses.COLOR_GREEN, bg)
        curses.init_pair(COLOR_PAIR_BEND, curses.COLOR_RED, bg)
        for i, color in enumerate(PIANO_ROLL_CHANNEL_COLORS):
            curses.init_pair(PIANO_ROLL_CHANNEL_PAIR_BASE + i, color, bg)
    except curses.error:
        return False
    return True


def channel_color_pair(channel):
    """Numero di color pair assegnato a un canale MIDI nella piano roll."""
    return PIANO_ROLL_CHANNEL_PAIR_BASE + (channel % len(PIANO_ROLL_CHANNEL_COLORS))


# Blocchi Unicode a ottavi, dal vuoto al pieno: permettono una barra di
# avanzamento "morbida" invece di scattare di una cella intera per volta.
PROGRESS_BLOCK_LEVELS = " ▏▎▍▌▋▊▉█"


def draw_progress_bar(width, fraction):
    fraction = max(0.0, min(1.0, fraction))
    eighths_total = round(fraction * width * 8)
    full_cells, remainder = divmod(eighths_total, 8)
    full_cells = min(full_cells, width)
    bar = PROGRESS_BLOCK_LEVELS[-1] * full_cells
    if full_cells < width:
        bar += PROGRESS_BLOCK_LEVELS[remainder]
        bar += " " * (width - full_cells - 1)
    return bar


def draw_line(stdscr, cache, row, col, text, attr=curses.A_NORMAL, key=None, clear_to_eol=True):
    """Disegna una riga solo se il contenuto (testo+attributo+colonna) è
    cambiato rispetto al frame precedente. Evita l'erase()+redraw completo
    ad ogni ciclo (150ms), che genera flickering e consuma CPU inutilmente:
    aggiorna solo le righe le cui variabili sono realmente cambiate.

    'key' identifica la voce nella cache: di default è il numero di riga,
    utile quando più chiamate concorrono sullo stesso numero di riga fisica
    (es. le singole celle della piano roll, disegnate una per lane).

    'clear_to_eol' va disattivato (False) quando più chiamate indipendenti
    disegnano segmenti diversi sulla STESSA riga fisica (piano roll: una
    chiamata per ogni lane/nota): altrimenti clrtoeol() cancellerebbe anche
    le celle già disegnate a destra in un frame precedente e non ridisegnate
    in questo (perché il loro contenuto non è cambiato ed è stato quindi
    filtrato dalla cache) - il sintomo è proprio quello di celle/etichette
    già "accese" che spariscono non appena una cella alla loro sinistra
    cambia.
    """
    cache_key = row if key is None else key
    entry = (col, text, attr)
    if cache.get(cache_key) == entry:
        return
    try:
        stdscr.move(row, col)
        if clear_to_eol:
            stdscr.clrtoeol()
        stdscr.addstr(row, col, text, attr)
    except curses.error:
        pass
    cache[cache_key] = entry


PITCH_CLASS_LABELS = ["Do", "D#", "Re", "R#", "Mi", "Fa", "F#", "So", "S#", "La", "L#", "Si"]

# Caratteri della piano roll. Ogni nota "in arrivo" viene disegnata come un
# blocco verticale la cui altezza (in righe) rispecchia la sua durata reale,
# non un semplice trattino: la forma del blocco cambia in base a quanti
# "tick" di griglia della piano roll occupa (vedi PIANO_ROLL_LOOKAHEAD/
# PIANO_ROLL_ROWS più sotto per cosa si intende qui per "tick"):
#   1 tick  -> un singolo blocco pieno isolato (nota staccatissima)
#   2 tick  -> estremità superiore + inferiore, senza corpo centrale
#   3+ tick -> estremità superiore, corpo pieno per i tick centrali, estremità inferiore
# Quando la nota tocca davvero la riga di riproduzione (playhead, l'ultima
# riga) il carattere su quella riga diventa sempre '#', a prescindere dalla
# forma del blocco: è il segnale univoco "la nota sta suonando adesso".
PIANO_ROLL_ATTACK_CHAR = "#"
PIANO_ROLL_STACCATO_CHAR = "\u25a0"
PIANO_ROLL_BLOCK_TOP = "\u2584"
PIANO_ROLL_BLOCK_BODY = "\u2588"
PIANO_ROLL_BLOCK_BOTTOM = "\u2580"
PIANO_ROLL_BEND_WINDOW = 0.15   # secondi entro cui un pitchwheel è considerato "in corso"


def _block_glyphs(span_rows):
    """Ritorna la lista di caratteri (dall'alto verso il basso, cioè dal più
    lontano nel tempo al più vicino) che compone il blocco di una nota in
    base a quante righe della piano roll occupa: vedi le costanti
    PIANO_ROLL_* sopra per il significato di ogni forma."""
    if span_rows <= 1:
        return [PIANO_ROLL_STACCATO_CHAR]
    if span_rows == 2:
        return [PIANO_ROLL_BLOCK_TOP, PIANO_ROLL_BLOCK_BOTTOM]
    return [PIANO_ROLL_BLOCK_TOP] + [PIANO_ROLL_BLOCK_BODY] * (span_rows - 2) + [PIANO_ROLL_BLOCK_BOTTOM]


def render_piano_roll(stdscr, cache, track, row0, col0, width, colors_enabled):
    """Disegna una mini piano-roll ASCII a colonne fisse: ogni colonna
    rappresenta sempre la stessa nota (non si sposta seguendo cosa sta
    suonando in quel momento, altrimenti sarebbe impossibile seguirla a
    colpo d'occhio) e in basso è etichettata con il nome della nota
    ("Do", "D#", "Re", ...). Ogni nota "in arrivo" scende come un BLOCCO
    (non un singolo punto) la cui altezza rispecchia la sua durata reale
    (vedi _block_glyphs sopra); quando la nota suona davvero, sull'ultima
    riga (il "playhead") il carattere diventa sempre '#'. Ogni nota è
    colorata in base al canale MIDI di provenienza (tipicamente melodia/
    basso/accompagnamento sono su canali diversi), se il terminale supporta
    i colori. L'etichetta della nota in basso si illumina con lo stesso
    colore quando la nota sta suonando in quell'istante, per seguire
    l'animazione a colpo d'occhio.

    Le righe "in arrivo" partizionano l'intero intervallo di anticipo
    (PIANO_ROLL_LOOKAHEAD secondi) in fasce di tempo CONTIGUE, una per riga
    ("un tick di griglia" nel senso usato da _block_glyphs): un evento di
    nota ricade sempre in esattamente una fascia/riga, non può "cadere in
    un buco" tra due finestre e sparire per un istante prima di suonare
    davvero.

    L'intervallo mostrato è sempre lo stesso per ogni brano: va dall'ottava
    PIANO_ROLL_START_OCTAVE per PIANO_ROLL_NUM_OCTAVES ottave (per default
    Do1-Si6), indipendentemente dal range di note effettivamente presenti
    nel file. Modificare quelle due costanti in cima al file per cambiare
    l'ampiezza mostrata.
    """
    draw_line(stdscr, cache, row0, col0, "Piano roll:", curses.A_UNDERLINE)

    low_note = (PIANO_ROLL_START_OCTAVE + 1) * 12          # es. ottava 1 -> nota MIDI 24 (Do1)
    lanes = PIANO_ROLL_NUM_OCTAVES * 12
    high_note = low_note + lanes - 1

    # larghezza di colonna: abbastanza per l'etichetta ("Do", "D#", ...) ma
    # ridotta automaticamente se lo spazio disponibile non basta per
    # l'intero intervallo di ottave richiesto (la riga viene poi troncata
    # alla larghezza reale del terminale, non deformata).
    lane_width = max(2, min(4, width // max(1, lanes)))

    t_now = track.elapsed()
    hi = bisect.bisect_right(track._note_span_on_times, t_now + PIANO_ROLL_LOOKAHEAD)
    # Non basta più "attacco non ancora avvenuto" (on_t >= t_now): una nota
    # lunga deve restare visibile ANCHE dopo l'attacco, finché non è del
    # tutto conclusa, altrimenti il blocco sparirebbe di colpo nell'istante
    # in cui tocca il playhead invece di scorrerci dentro gradualmente. Si
    # scartano quindi solo le note già del tutto concluse (off_t <= t_now);
    # quelle ancora in corso (anche iniziate molto prima di questa finestra)
    # restano candidate.
    upcoming = [s for s in track._note_spans[:hi] if s[1] > t_now]
    active_now = track.active_notes()  # lista di (canale, nota)
    active_by_note = {note: ch for ch, note in active_now}

    # canali con un pitch bend "in corso" adesso: non cambia più il
    # carattere sul playhead (che resta sempre '#'), ma continua a
    # distinguersi con un colore dedicato invece del colore di canale.
    plo = bisect.bisect_left(track._pitch_event_times, t_now - PIANO_ROLL_BEND_WINDOW)
    phi = bisect.bisect_right(track._pitch_event_times, t_now + PIANO_ROLL_BEND_WINDOW)
    bending_channels = {ch for _, ch in track._pitch_events[plo:phi]}

    # Righe disponibili per le note "in arrivo" (tutte tranne l'ultima, che è
    # il playhead/"ora"): l'intervallo [0, PIANO_ROLL_LOOKAHEAD) viene diviso
    # in altrettante fasce di uguale durata ("i tick di griglia" della piano
    # roll). Per ogni nota calcoliamo la riga "di base" del blocco (foot_row)
    # in modo CONTINUO nel tempo, non solo finché l'attacco non è ancora
    # avvenuto: anche dopo l'attacco, foot_row continua a crescere oltre
    # l'ultima riga disponibile, così il blocco (ancorato a foot_row e esteso
    # verso l'alto per span_rows righe) continua a "scorrere dentro" il
    # playhead invece di sparire di colpo - la sua parte restante rimane
    # visibile finché non è stata interamente consumata.
    #
    # Se sulla STESSA lane (stessa altezza) cadono più occorrenze nella
    # finestra (es. due note staccate consecutive), vengono disegnate
    # TUTTE, non solo la più vicina: altrimenti la seconda comparirebbe dal
    # nulla solo dopo che la prima è stata consumata. Le righe vengono
    # quindi accumulate per lane invece di scegliere un'unica "vincitrice";
    # solo se due occorrenze si contendono la STESSA riga (capita se sono
    # molto ravvicinate nel tempo) vince quella più vicina/urgente, dando
    # priorità visiva a chi sta per suonare a breve.
    upcoming_rows = PIANO_ROLL_ROWS - 1
    bin_width = PIANO_ROLL_LOOKAHEAD / upcoming_rows
    note_block = {}  # nota -> {riga: (glifo, canale)}, accumulo di tutte le occorrenze in finestra
    # upcoming è già in ordine crescente di on_t (più lontana -> più vicina):
    # processandolo al contrario, l'update() finale lascia vincere sulle
    # righe in conflitto sempre l'occorrenza più vicina nel tempo.
    for on_t, off_t, on_tick, off_tick, ch, note in reversed(upcoming):
        # "quanti tick di griglia" occupa la nota: le note davvero cortissime
        # (1-2 tick MIDI grezzi, es. abbellimenti/grace note) restano sempre
        # un blocco minimo indipendentemente dalla scala del brano; le note
        # "normali" (3+ tick) vengono invece proporzionate alla loro durata
        # reale convertita nella griglia a righe della piano roll.
        tick_duration = off_tick - on_tick
        if tick_duration <= 1:
            span_rows = 1
        elif tick_duration == 2:
            span_rows = 2
        else:
            span_rows = max(1, round((off_t - on_t) / bin_width))

        remaining = on_t - t_now  # negativo se l'attacco è già avvenuto: non viene più troncato a 0
        foot_row = round((upcoming_rows - 1) - remaining / bin_width)
        top_row = foot_row - (span_rows - 1)

        glyphs = _block_glyphs(span_rows)
        rows_for_this_note = {}
        for offset, glyph in enumerate(glyphs):
            r = top_row + offset
            if 0 <= r <= upcoming_rows - 1:
                rows_for_this_note[r] = (glyph, ch)

        if not rows_for_this_note:
            continue  # nulla di visibile in questo istante (nota già del tutto "entrata")

        note_block.setdefault(note, {}).update(rows_for_this_note)

    for r in range(PIANO_ROLL_ROWS):
        is_now_row = r == PIANO_ROLL_ROWS - 1
        row = row0 + 1 + r
        for lane, note in enumerate(range(low_note, low_note + lanes)):
            col = col0 + lane * lane_width
            symbol = None
            channel = None
            is_bending = False
            if is_now_row and note in active_by_note:
                channel = active_by_note[note]
                is_bending = channel in bending_channels
                symbol = PIANO_ROLL_ATTACK_CHAR
            elif not is_now_row and note in note_block and r in note_block[note]:
                symbol, channel = note_block[note][r]
            if symbol is None:
                cell = " " * lane_width  # colonna vuota: niente puntini quando non c'è nulla in arrivo
                attr = curses.A_NORMAL
            else:
                cell = symbol.ljust(lane_width)
                attr = curses.A_BOLD if is_now_row else curses.A_NORMAL
                if colors_enabled:
                    pair = COLOR_PAIR_BEND if is_bending else channel_color_pair(channel)
                    attr |= curses.color_pair(pair)
            draw_line(stdscr, cache, row, col, cell, attr, key=("pianoroll_cell", r, lane), clear_to_eol=False)

    labels_row = row0 + 1 + PIANO_ROLL_ROWS
    for lane, note in enumerate(range(low_note, low_note + lanes)):
        col = col0 + lane * lane_width
        label = PITCH_CLASS_LABELS[note % 12].ljust(lane_width)
        if note in active_by_note:
            # la nota sta suonando adesso: l'etichetta si illumina con lo
            # stesso colore/intensità della cella nella riga "ora" sopra,
            # per seguire a colpo d'occhio l'animazione della piano roll
            channel = active_by_note[note]
            attr = curses.A_BOLD
            if colors_enabled:
                pair = COLOR_PAIR_BEND if channel in bending_channels else channel_color_pair(channel)
                attr |= curses.color_pair(pair)
        else:
            attr = curses.A_DIM
        draw_line(stdscr, cache, labels_row, col, label, attr, key=("pianoroll_label", lane), clear_to_eol=False)



def curses_prompt(stdscr, message):
    """Chiede una riga di testo all'utente restando dentro la sessione curses
    (usato per il percorso di un nuovo SoundFont o per un Program Change)."""
    stdscr.nodelay(False)
    stdscr.timeout(-1)
    curses.echo()
    curses.curs_set(1)
    h, w = stdscr.getmaxyx()
    try:
        stdscr.move(h - 1, 2)
        stdscr.clrtoeol()
        stdscr.addstr(h - 1, 2, message)
        stdscr.refresh()
        raw = stdscr.getstr(h - 1, 2 + len(message), 200)
        text = raw.decode("utf-8", errors="ignore").strip()
    except Exception:
        text = ""
    curses.noecho()
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(150)
    return text


def run_ui(stdscr, soundfont, audio_driver, gain, playlist,
           period_size, periods, sample_rate, no_effects):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(150)  # ms, controlla anche il refresh della UI
    colors_enabled = init_colors()

    track = FluidSynthTrack(soundfont, audio_driver, gain,
                             period_size=period_size, periods=periods,
                             sample_rate=sample_rate, no_effects=no_effects)
    track.start(playlist.current)

    status_msg = ""
    render_cache = {}
    prev_size = stdscr.getmaxyx()
    show_help = False  # la legenda comandi compare solo premendo ?/H, per lasciare più spazio verticale

    def load_index(i):
        playlist.index = i
        playlist.selected = i
        track.start(playlist.current)

    try:
        while True:
            h, w = stdscr.getmaxyx()
            if (h, w) != prev_size:
                # ridimensionamento del terminale: qui un erase() completo è
                # necessario (e accade di rado, quindi non pesa sulla CPU)
                stdscr.erase()
                render_cache.clear()
                prev_size = (h, w)

            title_bar = " MIDI Player (FluidSynth) "
            draw_line(stdscr, render_cache, 0, max(0, (w - len(title_bar)) // 2), title_bar, curses.A_BOLD)

            row = 2
            # Nome file (senza estensione) invece del titolo nei metadati: è
            # quasi sempre più affidabile/riconoscibile dei metadati interni,
            # spesso assenti o generici in molti file MIDI reali.
            file_stem = os.path.splitext(os.path.basename(playlist.current))[0]
            bpm_str = f"{track.current_bpm()} BPM"
            key_str = track.key_signature or "-"
            draw_line(stdscr, render_cache, row, 2,
                      f"In riproduzione: {file_stem}  |  {bpm_str}  |  Tonalita': {key_str}"[: w - 4])
            row += 1
            draw_line(stdscr, render_cache, row, 2,
                      (f"Autore/copyright: {track.author}" if track.author else "")[: w - 4])
            row += 2

            elapsed = track.elapsed()
            total = track.duration
            bar_width = max(10, w - 20)
            bar = draw_progress_bar(bar_width, elapsed / total if total else 0)
            progress_attr = curses.color_pair(COLOR_PAIR_PROGRESS) if colors_enabled else curses.A_NORMAL
            draw_line(stdscr, render_cache, row, 2,
                      f"{format_time(elapsed)} [{bar}] {format_time(total)}"[: w - 2], progress_attr)
            row += 2

            state = "PAUSA" if track.paused else "PLAY"
            draw_line(stdscr, render_cache, row, 2, f"Stato: {state}", progress_attr)
            row += 1

            info_line = (f"Volume: {track.gain:.2f}  Velocita': {track.speed:.2f}x  "
                         f"Trasposizione: {track.transpose:+d}  "
                         f"Loop: {LOOP_LABELS[playlist.loop_mode]}  "
                         f"Shuffle: {'ON' if playlist.shuffle else 'OFF'}")
            draw_line(stdscr, render_cache, row, 2, info_line[: w - 4])
            row += 1

            if track.muted_channels:
                muted_str = ", ".join(str(c + 1) for c in sorted(track.muted_channels))
            else:
                muted_str = "-"
            draw_line(stdscr, render_cache, row, 2, f"Canali mutati: {muted_str}"[: w - 4])
            row += 1

            active = track.active_notes()
            if active:
                names = [note_name(note) for _, note in active]
                notes_line = "Note: " + ", ".join(names)
            else:
                notes_line = "Note: -"
            draw_line(stdscr, render_cache, row, 2, notes_line[: w - 4])
            row += 1

            chord = guess_chord(note for _, note in active) if len(active) >= 2 else None
            chord_line = f"Accordo: {chord}" if chord else "Accordo: -"
            chord_attr = (curses.color_pair(COLOR_PAIR_CHORD) | curses.A_BOLD) if (colors_enabled and chord) else curses.A_NORMAL
            draw_line(stdscr, render_cache, row, 2, chord_line[: w - 4], chord_attr)
            row += 1

            lyric = track.current_lyric() or ""
            # Il simbolo unicode ♪ può non essere disponibile su terminali
            # non-UTF8 o build di curses non linkate con ncursesw: in tal
            # caso ripieghiamo su un prefisso ASCII senza far crashare il player.
            lyric_text = f"\u266a {lyric}" if lyric else ""
            try:
                draw_line(stdscr, render_cache, row, 2, lyric_text[: w - 4], curses.A_BOLD)
            except UnicodeError:
                draw_line(stdscr, render_cache, row, 2, (f"> {lyric}" if lyric else "")[: w - 4], curses.A_BOLD)
            row += 2

            render_piano_roll(stdscr, render_cache, track, row, 2, w - 2, colors_enabled)
            row += PIANO_ROLL_ROWS + 3  # header + righe + etichette note + una riga di spaziatura

            playlist_total = len(playlist.files)
            list_start_row = row + 1
            help_rows = 3 if show_help else 1  # righe riservate in fondo (vedi sotto)
            max_list_rows = max(1, h - list_start_row - help_rows - 1)

            # scorrimento: la finestra visibile segue la voce selezionata,
            # tenendola centrata quando la playlist non ci sta tutta a schermo
            if playlist_total <= max_list_rows:
                scroll_offset = 0
            else:
                scroll_offset = playlist.selected - max_list_rows // 2
                scroll_offset = max(0, min(scroll_offset, playlist_total - max_list_rows))

            header = "Playlist:"
            if playlist_total > max_list_rows:
                shown_first = scroll_offset + 1
                shown_last = min(scroll_offset + max_list_rows, playlist_total)
                header += f"  ({shown_first}-{shown_last}/{playlist_total})"
            draw_line(stdscr, render_cache, row, 2, header, curses.A_UNDERLINE)
            row += 1

            visible_indices = range(scroll_offset, min(scroll_offset + max_list_rows, playlist_total))
            for r, i in enumerate(visible_indices):
                fname = os.path.basename(playlist.files[i])
                is_playing = i == playlist.index
                is_selected = i == playlist.selected
                marker = "\u25b6" if is_playing else " "  # ▶
                attr = curses.A_NORMAL
                if colors_enabled and is_playing:
                    attr |= curses.color_pair(COLOR_PAIR_ACTIVE_TRACK) | curses.A_BOLD
                if is_selected:
                    attr |= curses.A_REVERSE
                line = f"{marker} {fname}"[: w - 4]
                draw_line(stdscr, render_cache, list_start_row + r, 4, line, attr)
            # ripulisce eventuali righe rimaste da una playlist scorsa più lunga
            # (es. dopo un ridimensionamento) che non vengono più riscritte qui sopra
            for r in range(len(visible_indices), max_list_rows):
                draw_line(stdscr, render_cache, list_start_row + r, 4, "")

            if show_help:
                help1 = ("SPAZIO pausa | <-/-> 5s | N/B brano | R riavvia | SU/GIU/INVIO playlist | "
                         "L loop | S shuffle")
                help2 = ("+/- volume | </> velocita' | [/] trasposizione | P programma | "
                         "1-9,0/F1-F6 muta canale | F7 SoundFont")
                help3 = "?/H nascondi comandi | Q esci"
                draw_line(stdscr, render_cache, h - 4, 2, help1[: w - 4])
                draw_line(stdscr, render_cache, h - 3, 2, help2[: w - 4])
                draw_line(stdscr, render_cache, h - 2, 2, help3[: w - 4])
            else:
                draw_line(stdscr, render_cache, h - 2, 2, "Premi ?/H per i comandi | Q per uscire"[: w - 4])
            draw_line(stdscr, render_cache, h - 1, 2, status_msg[: w - 4])

            stdscr.noutrefresh()
            curses.doupdate()

            # avanzamento automatico a fine brano
            if track.is_song_over():
                if playlist.loop_mode == "single":
                    track.restart()
                    status_msg = "Ripeti brano"
                elif playlist.next():
                    track.start(playlist.current)
                    status_msg = "Brano successivo"
                else:
                    track.finished = True
                    status_msg = "Fine della playlist"

            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if key == -1:
                continue
            elif key == curses.KEY_RESIZE:
                render_cache.clear()
                stdscr.erase()
            elif key in (ord("q"), ord("Q")):
                break
            elif key == ord(" "):
                track.toggle_pause()
                status_msg = "In pausa" if track.paused else "Ripresa riproduzione"
            elif key == curses.KEY_RIGHT:
                track.seek(SEEK_STEP)
                status_msg = f"Avanti di {int(SEEK_STEP)}s"
            elif key == curses.KEY_LEFT:
                track.seek(-SEEK_STEP)
                status_msg = f"Indietro di {int(SEEK_STEP)}s"
            elif key in (ord("n"), ord("N")):
                if playlist.next():
                    track.start(playlist.current)
                    status_msg = "Brano successivo"
                else:
                    status_msg = "Sei gia' all'ultimo brano"
            elif key in (ord("b"), ord("B")):
                if playlist.prev():
                    track.start(playlist.current)
                    status_msg = "Brano precedente"
                else:
                    status_msg = "Sei gia' al primo brano"
            elif key in (ord("r"), ord("R")):
                track.restart()
                status_msg = "Brano riavviato"
            elif key == curses.KEY_UP:
                playlist.selected = max(0, playlist.selected - 1)
            elif key == curses.KEY_DOWN:
                playlist.selected = min(len(playlist.files) - 1, playlist.selected + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                load_index(playlist.selected)
                status_msg = f"Riproduzione: {os.path.basename(playlist.current)}"
            elif key in (ord("+"), ord("="), curses.KEY_PPAGE):
                track.change_gain(GAIN_STEP)
                status_msg = f"Volume: {track.gain:.2f}"
            elif key in (ord("-"), ord("_"), curses.KEY_NPAGE):
                track.change_gain(-GAIN_STEP)
                status_msg = f"Volume: {track.gain:.2f}"
            elif key == ord(">"):
                track.change_speed(SPEED_STEP)
                status_msg = f"Velocita': {track.speed:.2f}x"
            elif key == ord("<"):
                track.change_speed(-SPEED_STEP)
                status_msg = f"Velocita': {track.speed:.2f}x"
            elif key == ord("]"):
                track.change_transpose(TRANSPOSE_STEP)
                status_msg = f"Trasposizione: {track.transpose:+d} semitoni"
            elif key == ord("["):
                track.change_transpose(-TRANSPOSE_STEP)
                status_msg = f"Trasposizione: {track.transpose:+d} semitoni"
            elif key in (ord("l"), ord("L")):
                mode = playlist.cycle_loop_mode()
                status_msg = f"Modalita' ripetizione: {LOOP_LABELS[mode]}"
            elif key in (ord("s"), ord("S")):
                enabled = playlist.toggle_shuffle()
                status_msg = "Shuffle attivato" if enabled else "Shuffle disattivato"
            elif key in (ord("?"), ord("h"), ord("H")):
                show_help = not show_help
                render_cache.clear()
                stdscr.erase()
            elif key in (ord("p"), ord("P")):
                raw = curses_prompt(stdscr, "Canale Programma (es. 1 41): ")
                render_cache.clear()
                stdscr.erase()
                try:
                    ch_str, prog_str = raw.split()
                    channel = int(ch_str) - 1
                    program = int(prog_str)
                    if 0 <= channel <= 15 and 0 <= program <= 127:
                        track.set_program(channel, program)
                        status_msg = f"Canale {channel + 1}: programma {program}"
                    else:
                        status_msg = "Valori fuori range (canale 1-16, programma 0-127)"
                except Exception:
                    status_msg = "Formato non valido: usa 'canale programma', es. '1 41'"
            elif key == curses.KEY_F7:
                new_sf = curses_prompt(stdscr, "Nuovo SoundFont (.sf2): ")
                render_cache.clear()
                stdscr.erase()
                if new_sf and track.change_soundfont(new_sf):
                    status_msg = f"SoundFont caricato: {os.path.basename(new_sf)}"
                elif new_sf:
                    status_msg = "Impossibile caricare il SoundFont indicato"
            elif ord("0") <= key <= ord("9"):
                # tasti 1-9 -> canali MIDI 1-9 (indici 0-8), tasto 0 -> canale 10 (indice 9)
                digit = key - ord("0")
                channel = 9 if digit == 0 else digit - 1
                track.toggle_channel_mute(channel)
                muted = channel in track.muted_channels
                status_msg = f"Canale {channel + 1}: {'mutato' if muted else 'riattivato'}"
            elif curses.KEY_F1 <= key <= curses.KEY_F6:
                # un file MIDI ha fino a 16 canali: i tasti numerici coprono solo
                # i primi 10, per gli ulteriori 6 (11-16) usiamo F1-F6
                channel = 10 + (key - curses.KEY_F1)
                track.toggle_channel_mute(channel)
                muted = channel in track.muted_channels
                status_msg = f"Canale {channel + 1}: {'mutato' if muted else 'riattivato'}"
    finally:
        track.stop_process()


def export_to_wav(path, soundfont, out_path, gain=0.5, sample_rate=44100,
                   no_effects=False, tail_seconds=2.0):
    """Renderizza un file MIDI in un WAV offline: nessun driver audio viene
    avviato, i campioni sono generati con get_samples() alla massima
    velocità della CPU invece che in tempo reale.
    """
    info = analyze_midi(path)
    events = info["events"]

    synth = fluidsynth.Synth(gain=gain, samplerate=float(sample_rate))
    with _suppress_native_output():
        if no_effects:
            synth.setting("synth.reverb.active", 0)
            synth.setting("synth.chorus.active", 0)
        sfid = synth.sfload(soundfont)
    for ch in range(16):
        synth.program_select(ch, sfid, 0, 0)

    wav = wave.open(out_path, "wb")
    wav.setnchannels(2)
    wav.setsampwidth(2)  # 16 bit
    wav.setframerate(sample_rate)

    try:
        current_sample = 0
        for t, msg in events:
            target_sample = int(t * sample_rate)
            n = target_sample - current_sample
            if n > 0:
                samples = synth.get_samples(n)
                wav.writeframesraw(samples.tobytes())
                current_sample = target_sample

            ch = msg.channel
            if msg.type == "note_on" and msg.velocity > 0:
                synth.noteon(ch, msg.note, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                synth.noteoff(ch, msg.note)
            elif msg.type == "control_change":
                synth.cc(ch, msg.control, msg.value)
            elif msg.type == "program_change":
                synth.program_change(ch, msg.program)
            elif msg.type == "pitchwheel":
                synth.pitch_bend(ch, msg.pitch + 8192)

        tail_samples = int(tail_seconds * sample_rate)
        if tail_samples > 0:
            wav.writeframesraw(synth.get_samples(tail_samples).tobytes())
    finally:
        wav.close()
        synth.delete()


def main():
    parser = argparse.ArgumentParser(description="Player MIDI da terminale basato su FluidSynth")
    parser.add_argument("files", nargs="*", help="File .mid/.midi da riprodurre")
    parser.add_argument("--dir", help="Cartella da cui caricare tutti i file .mid/.midi")
    parser.add_argument("--soundfont", help="Percorso del file SoundFont (.sf2)")
    parser.add_argument("--audio-driver", default=None,
                         help="Driver audio per fluidsynth (es. alsa, pulseaudio, coreaudio, dsound)")
    parser.add_argument("--gain", type=float, default=0.5, help="Guadagno audio (default 0.5)")
    parser.add_argument("--period-size", type=int, default=1024,
                         help="Dimensione del buffer audio in campioni (default 1024; "
                              "aumentalo, es. 2048 o 4096, se l'audio gracchia)")
    parser.add_argument("--periods", type=int, default=4,
                         help="Numero di buffer audio (default 4; aumentalo se l'audio gracchia)")
    parser.add_argument("--sample-rate", type=int, default=None,
                         help="Forza una frequenza di campionamento (es. 44100 o 48000); "
                              "utile se l'audio è distorto per un mismatch col driver audio")
    parser.add_argument("--no-effects", action="store_true",
                         help="Disattiva riverbero e chorus per ridurre il carico sulla CPU "
                              "(utile su macchine lente se l'audio gracchia)")
    parser.add_argument("--shuffle", action="store_true", help="Avvia la playlist in modalità casuale")
    parser.add_argument("--loop", choices=["off", "all", "single"], default="off",
                         help="Modalità di ripetizione iniziale (default: off)")
    parser.add_argument("--export", metavar="OUTPUT",
                         help="Non avvia l'interfaccia: renderizza i file MIDI in WAV (rendering "
                              "offline, più veloce del tempo reale). Con un solo file, OUTPUT è il "
                              "percorso del .wav; con più file, OUTPUT è una cartella di destinazione.")
    args = parser.parse_args()

    cfg = load_config()

    # --- SoundFont: usa l'argomento da riga di comando, altrimenti chiedi ---
    if args.soundfont:
        soundfont = args.soundfont
    else:
        last_soundfont = cfg.get("soundfont") or find_default_soundfont()
        soundfont = prompt_with_default("SoundFont (.sf2)", last_soundfont)

    if not soundfont or not os.path.isfile(soundfont):
        print(f"Errore: SoundFont non trovato: '{soundfont}'")
        print("Su Debian/Ubuntu puoi installarne uno con: sudo apt install fluid-soundfont-gm")
        sys.exit(1)

    # --- File/cartella MIDI: usa gli argomenti da riga di comando, altrimenti chiedi ---
    files = list(args.files)
    if args.dir:
        midi_input = args.dir
    elif files:
        midi_input = None  # già specificati sulla riga di comando
    else:
        last_midi_path = cfg.get("midi_path")
        midi_input = prompt_with_default(
            "Cartella o file MIDI (separa più file con uno spazio)", last_midi_path
        )

    if midi_input:
        if os.path.isdir(midi_input):
            for f in sorted(os.listdir(midi_input)):
                if f.lower().endswith((".mid", ".midi")):
                    files.append(os.path.join(midi_input, f))
        else:
            files.extend(shlex.split(midi_input))

    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("Nessun file .mid/.midi trovato. Specifica una cartella o dei file validi.")
        sys.exit(1)

    # salva le scelte per la prossima esecuzione
    cfg["soundfont"] = soundfont
    if midi_input:
        cfg["midi_path"] = midi_input
    save_config(cfg)

    if args.export:
        sample_rate = args.sample_rate or 44100
        if len(files) == 1:
            targets = [(files[0], args.export)]
        else:
            os.makedirs(args.export, exist_ok=True)
            targets = [
                (f, os.path.join(args.export, os.path.splitext(os.path.basename(f))[0] + ".wav"))
                for f in files
            ]
        for src, dst in targets:
            print(f"Rendering '{src}' -> '{dst}' ...")
            try:
                export_to_wav(src, soundfont, dst, gain=args.gain,
                               sample_rate=sample_rate, no_effects=args.no_effects)
            except Exception as e:
                print(f"  Errore durante l'esportazione: {e}")
        return

    playlist = Playlist(files)
    playlist.shuffle = args.shuffle
    playlist.loop_mode = args.loop

    # Non solo all'avvio: durante la riproduzione FluidSynth può scrivere
    # avvisi direttamente sul file descriptor dello stderr di sistema (es.
    # "Instrument not found on channel N ... substituted ..." quando un
    # program change punta a un preset assente nel SoundFont). Scrivendo a
    # basso livello, questi messaggi bypassano completamente curses e
    # spostano/rovinano l'intera interfaccia. Copriamo perciò tutta la
    # sessione curses, non solo il caricamento del SoundFont.
    with _suppress_native_output():
        curses.wrapper(
            run_ui, soundfont, args.audio_driver, args.gain, playlist,
            args.period_size, args.periods, args.sample_rate, args.no_effects,
        )

    print("Riproduzione terminata.")


if __name__ == "__main__":
    main()
