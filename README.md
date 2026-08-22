# 🎵 FluidSynth Terminal MIDI Player

A lightweight, responsive, and elegant command-line MIDI player written in Python. It features a TUI (Terminal User Interface) built with `curses` and drives the **FluidSynth** audio synthesis engine via its Python bindings (`pyFluidSynth`).

---

## Benchmark & Resource Footprint
Tested under strict Linux `systemd-run` cgroup limits:
* **CPU Usage:** Flawless real-time playback down to **2% of a single core** with default buffer settings. Responsiveness is instantaneous thanks to event-based threading that eliminates polling overhead.
* **RAM Footprint:** ~264 MB total (using a ~240 MB GM SoundFont). Memory management is highly optimized, utilizing zero-allocation chord detection (`frozenset`) and `itertools.islice` to prevent memory copying during piano roll rendering.
* **Robustness:** Starts and runs down to **1% CPU** (requires larger buffer size).

---

## Key Features

* **Curses TUI Interface**: A complete terminal-based graphical interface with no heavy GUI dependencies.
* **Real-time Visualization**:
  * **Chords and Notes**: Smart chord recognition during playback, including **inversion** detection (e.g., `C maj / E` when the lowest note is the bass rather than the root).
  * **Karaoke / Lyrics**: Extraction and synchronized display of lyrics and metadata (`title`, `copyright`, `lyrics`/`text`).
  * **Piano Roll**: ASCII-based visual representation of upcoming notes, colored by MIDI channel.
  * **Progress Bar**: Monitoring with elapsed time and total duration timestamps.
  * **Smart UI Formatting**: Elegant truncation (`...`) of excessively long track titles to preserve the layout structure.
* **Advanced Playback Controls**:
  * Play, pause, restart, and fast forward/rewind (± 5 seconds).
  * Dynamic adjustment of **playback speed** (from `0.25x` to `3.00x`).
  * On-the-fly **volume / gain** control.
  * **Transpose**: Shift pitch up or down by semitones (±24).
* **Quality of Life (QoL) Features**:
  * **Preventive Auto-Gain (Opt-in)**: Analyzes MIDI velocity and average polyphony upon file load to intelligently scale the synthesizer's gain multiplier, normalizing track volumes without expensive audio DSP overhead.
  * **State Persistence**: The application remembers your exact setup (SoundFont, master volume, shuffle/loop modes, and active QoL flags) across restarts.
* **Practice Mode (MIDI Channel Mute)**:
  * Instantly mute or unmute any of the 16 MIDI channels (keys `1-9`, `0` for drums/percussion on channel 10, `F1-F6` for channels 11-16).
  * Perfect for removing a specific track (e.g., piano or guitar) and using the playback as a backing track for practice.
  * Change instruments on the fly (Program Change).
* **Playlist Management**:
  * Play single `.mid`/`.midi` files or entire directories.
  * Support for **Deck Shuffle** (True Shuffle preventing repeated tracks until the queue finishes) and **Loop** modes (Off, Loop All, Loop Single).
  * **Quick Search**: Real-time playlist filtering to instantly find tracks in large libraries.
* **MPRIS Integration (Opt-in)**:
  * Support for system media controls (media keys, desktop audio widgets) via D-Bus session. 

---

## System Requirements

### 1. FluidSynth
The program requires the `libfluidsynth` native system library to be installed on your system.

* **Debian / Ubuntu**:
  ```bash
  sudo apt update
  sudo apt install libfluidsynth3 fluid-soundfont-gm
  ```
* **Arch Linux**:
  ```bash
  sudo pacman -S fluidsynth soundfont-fluid
  ```
* **macOS** (via Homebrew):
  ```bash
  brew install fluid-synth
  ```

### 2. SoundFont (.sf2)
A General MIDI SoundFont file in `.sf2` format is required (e.g., `FluidR3_GM.sf2`, which is often installed by the packages above, or any custom SoundFont).

### 3. Python Dependencies
The project relies mostly on the Python standard library (`curses`, `json`, `argparse`, etc.) and requires the following external dependencies:

* `mido`
* `pyFluidSynth`

```bash
pip install mido pyFluidSynth
```

### 4. Optional Dependencies (MPRIS System Controls)
To enable system media keys and desktop audio widget controls, `pydbus` and `PyGObject` are required:
* **Debian / Ubuntu**: `sudo apt install python3-pydbus python3-gi`
* **Pip**: `pip install pydbus PyGObject`

*(Note: If these are missing, the player will still function perfectly via keyboard without system controls).*

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/loridj11/CLI-midi-player.git
   cd CLI-midi-player
   ```

2. Ensure the dependencies are installed:
   ```bash
   pip install mido pyFluidSynth
   ```

3. Make the script executable (optional):
   ```bash
   chmod +x midiplayer.py
   ```

---

## Usage Guide

### Interactive Mode
If launched without arguments, the player will automatically search for common system SoundFonts and prompt the user to confirm or enter the desired paths:

```bash
python3 midiplayer.py
```

### Command Line (CLI) Launch

* **Play specific files:**
  ```bash
  python3 midiplayer.py --soundfont /path/to/FluidR3_GM.sf2 track1.mid track2.mid
  ```

* **Play an entire folder of MIDI files:**
  ```bash
  python3 midiplayer.py --soundfont /path/to/FluidR3_GM.sf2 --dir ./my_music
  ```

* **Launch with loop enabled:**
  ```bash
  python3 midiplayer.py --soundfont /path/to/FluidR3_GM.sf2 --dir ./midi --loop all --shuffle
  ```

* **Specify Audio Driver (e.g., pulseaudio, alsa, pipewire):**
  ```bash
  python3 midiplayer.py --soundfont FluidR3_GM.sf2 --dir ./midi --audio-driver pulseaudio
  ```

* **Offline Export to WAV (Fast rendering, no UI):**
  ```bash
  python3 midiplayer.py --soundfont FluidR3_GM.sf2 track.mid --export track.wav
  ```

---

## Keyboard Shortcuts

| Key | Action |
| :--- | :--- |
| `SPACE` | Pause / Resume playback |
| `←` / `→` | Skip backward / forward by 5 seconds |
| `N` / `B` | Skip to the next / previous track (navigates shuffle queue) |
| `R` | Restart the current track from the beginning |
| `↑` / `↓` | Navigate the playlist (visual selection) |
| `ENTER` | Start playing the selected track in the playlist |
| `+` / `-` | Increase / decrease volume (Gain) |
| `<` / `>` | Slow down / speed up playback (0.25x ... 3.00x) |
| `[` / `]` | Transpose down / up by a semitone (up to ±24) |
| `L` | Toggle Loop mode (`Off` -> `All` -> `Single`) |
| `S` | Enable / disable random playback (`Shuffle`) |
| `G` | Toggle Preventive Auto-Gain |
| `1` – `9` | Mute / unmute MIDI channels 1 to 9 |
| `0` | Mute / unmute MIDI channel 10 (Percussion / Drums) |
| `F1` – `F6` | Mute / unmute MIDI channels 11 to 16 |
| `F7` | Open Visual SoundFont Browser |
| `P` | Change instrument (Program Change) for a channel |
| `/` | Quick search / filter in the playlist |
| `?` / `H` | Show / hide the commands legend |
| `Q` | Quit the program |

---

## Advanced Startup Variables & Troubleshooting

If you experience audio crackling or distortion, you can tweak the engine's playback variables at startup:

* **Increase buffer size:** `--period-size 2048 --periods 6` (or higher)
* **Fix sample rate (e.g., for PipeWire):** `--sample-rate 48000`
* **Lower gain to avoid clipping:** `--gain 0.3`
* **Disable effects (reverb/chorus) on slower machines:** `--no-effects`
* **Enable MPRIS integration:** `--enable-mpris` 

*(Note: Explicit CLI flags for `--gain`, `--loop`, or `--shuffle` will override any saved states).*

---

## Legacy CLI Version

In the releases section of this repository, you can find older versions of this tool that utilize the `fluidsynth` CLI executable as an external process (via `subprocess`). Please note that this legacy version is now considered obsolete, is no longer supported, and will not receive any future updates.

---

## Configuration & State Files

Parameters and runtime preferences are saved across two JSON files in `~/.config/midiplayer/`:

1. **`config.json`**: Saves foundational preferences like the last used SoundFont and MIDI directory.
   Example file structure:
   ```json
   {
     "soundfont": "/usr/share/sounds/sf2/FluidR3_GM.sf2",
     "midi_path": "/home/user/Music/MIDI"
   }
   ```
2. **`state.json`**: Automatically saves the active playback state upon exiting, including volume, shuffle modes, loop statuses, and active QoL flags (such as Auto-Gain and MPRIS).

---

## TUI Preview:

![MidiPlayer Demo](assets/demo_cut.gif)

---

## Credits & Acknowledgments

This project relies on the following open-source software and components:

* **[FluidSynth](https://www.fluidsynth.org/)**: A real-time SoundFont 2 software synthesizer. Special thanks to the FluidSynth development team and contributors for maintaining such a powerful and versatile audio engine.
* **[pyFluidSynth](https://github.com/amberwhitehead/pyfluidsynth)**: Python bindings for FluidSynth.
* **[mido](https://github.com/mido/mido)**: MIDI objects for Python, used for parsing MIDI messages and tracks.
* **[pydbus](https://github.com/LEW21/pydbus)**: Pythonic D-Bus library, used to handle MPRIS D-Bus session integration.
* **[PyGObject](https://pygobject.gnome.org/)**: Python bindings for GObject Introspection, required to enable system media controls.

---

## License

Distributed under the GNU Lesser General Public License v3.0 (LGPLv3).
See `LICENSE` and `LICENSE.LESSER` for details.
