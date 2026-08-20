#!/usr/bin/env python3
"""
midiplayer.py - Player MIDI da terminale basato su FluidSynth
================================================================

Riproduce file .mid/.midi usando il binario "fluidsynth" come motore
audio, pilotato tramite la sua shell interattiva (comandi player_*
disponibili da FluidSynth 2.2.0 in poi).

Requisiti:
    - fluidsynth installato e nel PATH (versione >= 2.2.0 per il controllo
      di velocità "player_tempo_int"; le altre funzioni lavorano anche con
      versioni precedenti)
        Debian/Ubuntu: sudo apt install fluidsynth fluid-soundfont-gm
        Arch:          sudo pacman -S fluidsynth soundfont-fluid
        macOS:         brew install fluid-synth
    - un SoundFont (.sf2), es. FluidR3_GM.sf2
    - libreria python "mido" per leggere durata/tempo/metadati dei file MIDI
        pip install mido

Uso:
    python3 midiplayer.py
        (all'avvio chiede il percorso del SoundFont e la cartella/file MIDI;
         lasciando il campo vuoto viene riusato l'ultimo valore indicato,
         salvato in ~/.config/midiplayer/config.json)

    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 brano1.mid brano2.mid
    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 --dir ./cartella_midi
    python3 midiplayer.py --soundfont /percorso/FluidR3_GM.sf2 --dir ./midi --audio-driver pulseaudio

Comandi da tastiera:
    SPAZIO      Pausa / Riprendi
    <- / ->     Indietro / Avanti di 5 secondi nel brano corrente
    N           Salta al brano successivo
    B           Torna al brano precedente
    R           Torna all'inizio del brano corrente
    SU / GIU    Naviga la playlist (selezione)
    INVIO       Riproduce il brano selezionato nella playlist
    + / -       Alza / abbassa il volume (PagSu/PagGiu equivalenti)
    < / >       Rallenta / velocizza la riproduzione
    L           Cambia modalità di ripetizione (Off -> Tutti -> Singolo)
    S           Attiva/disattiva la riproduzione casuale (Shuffle)
    1-9, 0      Silenzia/riattiva il canale MIDI 1-10 (0 = canale 10,
                di solito le percussioni) - utile come base per esercitarsi
    F1-F6       Silenzia/riattiva i canali MIDI 11-16
    Q           Esci

Durante la riproduzione vengono mostrate anche le note attualmente in
esecuzione (calcolate leggendo in anticipo il file MIDI, canale
percussioni escluso), l'accordo riconosciuto (con eventuale rivolto,
es. "Do maj / Mi" quando il basso non è la fondamentale), il titolo e
l'autore/copyright letti dai metadati del file e, quando presenti, i
testi (lyrics) sincronizzati come in un karaoke.

Se l'audio gracchia/distorce:
    - aumenta il buffer: --period-size 2048 --periods 6 (o valori più alti)
    - se il tuo sistema usa PipeWire, prova a fissare la frequenza:
      --sample-rate 48000
    - abbassa il volume per evitare clipping: --gain 0.3
    - su macchine lente, disattiva gli effetti: --no-effects
    - prova un driver audio diverso: --audio-driver pulseaudio (o alsa/pipewire)
"""

import argparse
import curses
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time

try:
    import mido
except ImportError:
    print("Questo programma richiede la libreria 'mido'.\nInstallala con: pip install mido")
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
# quindi lo escludiamo dal riconoscimento di note/accordi.
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

    Il matching è basato su sottoinsiemi (subset) e non sull'uguaglianza
    esatta: con accordi pianistici estesi su più ottave (raddoppi, note di
    passaggio, tensioni non catalogate) le classi di altezza attive possono
    essere molte più delle 3-5 previste dai template. Cerchiamo quindi, per
    ogni possibile fondamentale, il template più esteso (specifico) che sia
    interamente CONTENUTO tra le note suonate: eventuali note aggiuntive
    vengono tollerate invece di far fallire il riconoscimento, mentre a
    parità di specificità viene preferito il template la cui fondamentale
    coincide con la nota al basso (ipotesi più probabile in posizione
    fondamentale).
    """
    notes = sorted(set(notes))
    if len(notes) < 2:
        return None

    pitch_classes = sorted(set(n % 12 for n in notes))
    bass_pitch_class = min(notes) % 12

    best = None  # (num_note_template, nome, root)
    for root in pitch_classes:
        intervals = set((pc - root) % 12 for pc in pitch_classes)
        for name, template in CHORD_TEMPLATES:
            template_set = set(template)
            if not template_set.issubset(intervals):
                continue
            score = len(template_set)
            if best is None or score > best[0] or (
                score == best[0] and root == bass_pitch_class and best[2] != bass_pitch_class
            ):
                best = (score, name, root)

    if best is None:
        return None

    _, name, root = best
    chord_name = f"{NOTE_NAMES[root]} {name}"
    if bass_pitch_class != root:
        chord_name += f" / {NOTE_NAMES[bass_pitch_class]}"
    return chord_name


def analyze_midi(path):
    """Analizza un file MIDI ed estrae tutte le informazioni utili al player.

    Ritorna un dizionario con:
        duration        durata totale in secondi
        ticks_per_beat  risoluzione del file
        tempo           tempo iniziale (microsecondi per beat)
        note_events     lista ordinata di (tempo_secondi, 'on'/'off', canale, nota)
        title           titolo del brano (dal primo evento 'track_name' utile), o None
        author          autore/copyright (dal primo evento 'copyright'), o None
        lyrics          lista ordinata di (tempo_secondi, testo) per la modalità karaoke
    """
    empty = {
        "duration": 0.0, "ticks_per_beat": 480, "tempo": 500000,
        "note_events": [], "title": None, "author": None, "lyrics": [],
    }
    try:
        mid = mido.MidiFile(path)
    except Exception:
        return empty

    duration = mid.length  # mido calcola già tenendo conto dei cambi di tempo
    ticks_per_beat = mid.ticks_per_beat or 480

    tempo = 500000  # default: 120 bpm
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break
        else:
            continue
        break

    title = None
    author = None
    note_events = []
    lyric_events = []   # eventi 'lyrics' veri e propri (karaoke standard)
    text_events = []    # eventi 'text' generici, usati come ripiego
    elapsed = 0.0
    try:
        for msg in mid:  # iterare su mid (non su mid.tracks) fonde le tracce e converte msg.time in secondi
            elapsed += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                note_events.append((elapsed, "on", msg.channel, msg.note))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                note_events.append((elapsed, "off", msg.channel, msg.note))
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
    except Exception:
        note_events = []

    # Alcuni file "karaoke" salvano i testi come semplici eventi 'text'
    # invece che 'lyrics': li usiamo solo se non ci sono lyrics vere e proprie.
    lyrics = lyric_events if lyric_events else text_events
    lyrics.sort(key=lambda item: item[0])

    return {
        "duration": duration, "ticks_per_beat": ticks_per_beat, "tempo": tempo,
        "note_events": note_events, "title": title, "author": author, "lyrics": lyrics,
    }


class FluidSynthTrack:
    """Gestisce il processo fluidsynth per UN singolo file MIDI alla volta."""

    def __init__(self, soundfont, audio_driver, gain, logfile,
                 period_size=1024, periods=4, sample_rate=None, no_effects=False):
        self.soundfont = soundfont
        self.audio_driver = audio_driver
        self.gain = gain
        self.logfile = logfile
        self.period_size = period_size
        self.periods = periods
        self.sample_rate = sample_rate
        self.no_effects = no_effects
        self.proc = None

        self.duration = 0.0
        self.ticks_per_beat = 480
        self.tempo = 500000  # microsecondi per beat
        self.note_events = []
        self.title = None
        self.author = None
        self.lyrics = []

        self.paused = False
        self.finished = False

        # "Orologio" interno del brano: la posizione (in secondi "di brano")
        # viene ricostruita come song_pos_al_ancoraggio + tempo_reale_trascorso * velocità.
        # Questo permette di tenere sincronizzati barra di progresso e testi
        # anche quando la velocità di riproduzione cambia a runtime.
        self._song_pos = 0.0
        self._anchor = time.monotonic()
        self.speed = 1.0

        # Mute/Solo dei canali MIDI (modalità "esercizio"/backing track).
        self.muted_channels = set()
        self._channel_volume = {}  # canale -> ultimo volume noto prima del mute

    # ---- gestione processo -------------------------------------------------
    def start(self, path):
        self.stop_process()

        info = analyze_midi(path)
        self.duration = info["duration"]
        self.ticks_per_beat = info["ticks_per_beat"]
        self.tempo = info["tempo"]
        self.note_events = info["note_events"]
        self.title = info["title"]
        self.author = info["author"]
        self.lyrics = info["lyrics"]

        self.paused = False
        self.finished = False
        self._song_pos = 0.0
        self._anchor = time.monotonic()

        cmd = ["fluidsynth", "-n", "-g", str(self.gain)]
        if self.audio_driver:
            cmd += ["-a", self.audio_driver]
        if self.sample_rate:
            cmd += ["-r", str(self.sample_rate)]
        # Buffer audio più ampio: riduce (o elimina) il gracchiare dovuto a
        # underrun. Ininfluente sulla precisione ritmica perché qui si
        # riproducono sempre file (player interno), mai eventi MIDI live.
        cmd += ["-o", f"audio.period-size={self.period_size}",
                "-o", f"audio.periods={self.periods}"]
        if self.no_effects:
            # riverbero/chorus sono i consumatori di CPU più pesanti: disattivarli
            # aiuta su macchine lente dove il gracchiare è dovuto a CPU satura
            cmd += ["-R", "0", "-C", "0"]
        cmd += [self.soundfont, path]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=self.logfile,
            stderr=self.logfile,
            text=True,
            bufsize=1,
        )

        # Il nuovo processo fluidsynth parte con velocità e canali "puliti":
        # riapplichiamo le impostazioni persistenti scelte dall'utente.
        if self.speed != 1.0:
            self._send(f"player_tempo_int {self.speed:.2f}")
        for channel in self.muted_channels:
            self._send(f"cc {channel} 7 0")

    def stop_process(self):
        if self.proc and self.proc.poll() is None:
            try:
                self._send("quit")
                self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def _send(self, cmd):
        if self.proc and self.proc.stdin and self.proc.poll() is None:
            try:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    # ---- controlli di riproduzione ------------------------------------------
    def pause(self):
        if not self.paused:
            self._song_pos = self.elapsed()
            self.paused = True
            self._send("player_stop")

    def resume(self):
        if self.paused:
            self._anchor = time.monotonic()
            self.paused = False
            self._send("player_cont")

    def toggle_pause(self):
        self.pause() if not self.paused else self.resume()

    def restart(self):
        self._song_pos = 0.0
        self._anchor = time.monotonic()
        self.paused = False
        self.finished = False
        self._send("player_start")
        if self.speed != 1.0:
            self._send(f"player_tempo_int {self.speed:.2f}")

    def seek(self, delta_seconds):
        current = self.elapsed()
        target = max(0.0, min(self.duration, current + delta_seconds))
        actual_delta = target - current
        if actual_delta == 0:
            return

        beats_per_second = 1_000_000 / self.tempo
        ticks_per_second = beats_per_second * self.ticks_per_beat
        tick_delta = int(round(actual_delta * ticks_per_second))
        if tick_delta == 0:
            return

        self._song_pos = target
        self._anchor = time.monotonic()

        sign = "+" if tick_delta > 0 else ""
        self._send(f"player_seek {sign}{tick_delta}")

    def elapsed(self):
        if self.paused:
            return self._song_pos
        return self._song_pos + (time.monotonic() - self._anchor) * self.speed

    def is_song_over(self):
        return (not self.paused) and self.duration > 0 and self.elapsed() >= self.duration

    # ---- volume --------------------------------------------------------------
    def change_gain(self, delta):
        self.gain = max(GAIN_MIN, min(GAIN_MAX, self.gain + delta))
        self._send(f"gain {self.gain:.2f}")

    # ---- velocità di riproduzione ---------------------------------------------
    def change_speed(self, delta):
        # arrotondiamo per restare sempre sulla stessa "griglia" di valori
        # (altrimenti gli errori di virgola mobile o un valore di clamp non
        # allineato al passo impedirebbero di tornare esattamente a 1.0x)
        new_speed = round(self.speed + delta, 2)
        new_speed = round(max(SPEED_MIN, min(SPEED_MAX, new_speed)), 2)
        if new_speed == self.speed:
            return
        # "congela" la posizione corrente nel brano prima di cambiare velocità,
        # così elapsed() resta coerente e sincronizzato con l'audio reale.
        self._song_pos = self.elapsed()
        self._anchor = time.monotonic()
        self.speed = new_speed
        self._send(f"player_tempo_int {self.speed:.2f}")

    # ---- note e accordi --------------------------------------------------------
    def active_notes(self):
        """Ritorna la lista (canale, nota) attive nell'istante corrente, note melodiche escluse le percussioni."""
        t = self.elapsed()
        active = {}
        for evt_time, kind, channel, note in self.note_events:
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
        if channel in self.muted_channels:
            self.muted_channels.discard(channel)
            volume = self._channel_volume.get(channel, 100)
            self._send(f"cc {channel} 7 {volume}")
        else:
            self.muted_channels.add(channel)
            self._send(f"cc {channel} 7 0")


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


def draw_progress_bar(width, fraction):
    fraction = max(0.0, min(1.0, fraction))
    filled = int(width * fraction)
    return "#" * filled + "-" * (width - filled)


def run_ui(stdscr, soundfont, audio_driver, gain, playlist, logfile,
           period_size, periods, sample_rate, no_effects):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(150)  # ms, controlla anche il refresh della UI

    track = FluidSynthTrack(soundfont, audio_driver, gain, logfile,
                             period_size=period_size, periods=periods,
                             sample_rate=sample_rate, no_effects=no_effects)
    track.start(playlist.current)

    status_msg = ""

    def load_index(i):
        playlist.index = i
        playlist.selected = i
        track.start(playlist.current)

    try:
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            title_bar = " MIDI Player (FluidSynth) "
            stdscr.addstr(0, max(0, (w - len(title_bar)) // 2), title_bar, curses.A_BOLD)

            row = 2
            display_title = track.title or os.path.basename(playlist.current)
            stdscr.addstr(row, 2, f"In riproduzione: {display_title}"[: w - 4])
            row += 1
            if track.author:
                stdscr.addstr(row, 2, f"Autore/copyright: {track.author}"[: w - 4])
                row += 1
            row += 1

            elapsed = track.elapsed()
            total = track.duration
            bar_width = max(10, w - 20)
            bar = draw_progress_bar(bar_width, elapsed / total if total else 0)
            stdscr.addstr(row, 2, f"{format_time(elapsed)} [{bar}] {format_time(total)}"[: w - 2])
            row += 2

            state = "PAUSA" if track.paused else "PLAY"
            stdscr.addstr(row, 2, f"Stato: {state}")
            row += 1

            loop_line = (f"Volume: {track.gain:.2f}  Velocita': {track.speed:.2f}x  "
                         f"Loop: {LOOP_LABELS[playlist.loop_mode]}  "
                         f"Shuffle: {'ON' if playlist.shuffle else 'OFF'}")
            stdscr.addstr(row, 2, loop_line[: w - 4])
            row += 1

            if track.muted_channels:
                muted_str = ", ".join(str(c + 1) for c in sorted(track.muted_channels))
            else:
                muted_str = "-"
            stdscr.addstr(row, 2, f"Canali mutati: {muted_str}"[: w - 4])
            row += 1

            active = track.active_notes()
            if active:
                names = [note_name(note) for _, note in active]
                notes_line = "Note: " + ", ".join(names)
            else:
                notes_line = "Note: -"
            stdscr.addstr(row, 2, notes_line[: w - 4])
            row += 1

            chord = guess_chord(note for _, note in active) if len(active) >= 2 else None
            chord_line = f"Accordo: {chord}" if chord else "Accordo: -"
            stdscr.addstr(row, 2, chord_line[: w - 4])
            row += 1

            lyric = track.current_lyric()
            if lyric:
                # Il simbolo unicode ♪ può non essere disponibile su terminali
                # non-UTF8 o su build di curses non linkate con ncursesw: in tal
                # caso addstr solleva un'eccezione, che qui non deve mai far
                # crashare il player (si ripiega su un prefisso ASCII).
                try:
                    stdscr.addstr(row, 2, f"\u266a {lyric}"[: w - 4], curses.A_BOLD)
                except (curses.error, UnicodeError):
                    try:
                        stdscr.addstr(row, 2, f"> {lyric}"[: w - 4], curses.A_BOLD)
                    except curses.error:
                        pass
            row += 2

            stdscr.addstr(row, 2, "Playlist:", curses.A_UNDERLINE)
            row += 1
            list_start_row = row
            max_list_rows = max(1, h - list_start_row - 5)
            for r, i in enumerate(range(len(playlist.files))):
                if r >= max_list_rows:
                    break
                fname = os.path.basename(playlist.files[i])
                marker = ">" if i == playlist.index else " "
                attr = curses.A_REVERSE if i == playlist.selected else curses.A_NORMAL
                line = f"{marker} {fname}"[: w - 4]
                stdscr.addstr(list_start_row + r, 4, line, attr)

            help1 = "SPAZIO pausa | <- -> avanti/indietro 5s | N succ. | B prec. | R riavvia"
            help2 = "SU/GIU seleziona | INVIO riproduci | L loop | S shuffle"
            help3 = "+/- volume | </> velocita' | 1-9,0 muta canale | F1-F6 canali 11-16 | Q esci"
            if h > list_start_row + max_list_rows + 4:
                stdscr.addstr(h - 4, 2, help1[: w - 4])
                stdscr.addstr(h - 3, 2, help2[: w - 4])
                stdscr.addstr(h - 2, 2, help3[: w - 4])
            if status_msg:
                stdscr.addstr(h - 1, 2, status_msg[: w - 4])

            stdscr.refresh()

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
            elif key in (ord("l"), ord("L")):
                mode = playlist.cycle_loop_mode()
                status_msg = f"Modalita' ripetizione: {LOOP_LABELS[mode]}"
            elif key in (ord("s"), ord("S")):
                enabled = playlist.toggle_shuffle()
                status_msg = "Shuffle attivato" if enabled else "Shuffle disattivato"
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
    args = parser.parse_args()

    if shutil.which("fluidsynth") is None:
        print("Errore: 'fluidsynth' non trovato nel PATH. Installalo prima di continuare.")
        sys.exit(1)

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

    playlist = Playlist(files)
    playlist.shuffle = args.shuffle
    playlist.loop_mode = args.loop
    logpath = "/tmp/midiplayer_fluidsynth.log"

    with open(logpath, "w") as logfile:
        curses.wrapper(
            run_ui, soundfont, args.audio_driver, args.gain, playlist, logfile,
            args.period_size, args.periods, args.sample_rate, args.no_effects,
        )

    print(f"Riproduzione terminata. Log di fluidsynth salvato in: {logpath}")


if __name__ == "__main__":
    main()
