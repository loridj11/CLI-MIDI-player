# 🎵 FluidSynth Terminal MIDI Player

A lightweight, responsive, and elegant command-line MIDI player written in Python. It features a TUI (Terminal User Interface) built with `curses` and drives the **FluidSynth** audio synthesis engine via its Python bindings (`pyFluidSynth`).

---

## Benchmark & Resource Footprint
Tested under strict Linux `systemd-run` cgroup limits:
* **CPU Usage:** Flawless real-time playback down to **2% of a single core** with default buffer settings.
* **RAM Footprint:** ~264 MB total (using a ~240 MB GM SoundFont).
* **Robustness:** Starts and runs down to **1% CPU** (requires larger buffer size).
  
---

## Key Features

* **Curses TUI Interface**: A complete terminal-based graphical interface with no heavy GUI dependencies.
* **Real-time Visualization**:
  * **Chords and Notes**: Smart chord recognition during playback, including **inversion** detection (e.g., `C maj / E` when the lowest note is the bass rather than the root).
  * **Karaoke / Lyrics**: Extraction and synchronized display of lyrics and metadata (`title`, `copyright`, `lyrics`/`text`).
  * **Progress Bar**: Monitoring with elapsed time and total duration timestamps.
* **Advanced Playback Controls**:
  * Play, pause, restart, and fast forward/rewind (± 5 seconds).
  * Dynamic adjustment of **playback speed** (from `0.25x` to `3.00x`).
  * On-the-fly **volume / gain** control.
* **Practice Mode (MIDI Channel Mute)**:
  * Instantly mute or unmute any of the 16 MIDI channels (keys `1-9`, `0` for drums/percussion on channel 10, `F1-F6` for channels 11-16).
  * Perfect for removing a specific track (e.g., piano or guitar) and using the playback as a backing track for practice.
* **Playlist Management**:
  * Play single `.mid`/`.midi` files or entire directories.
  * Support for **Shuffle** and **Loop** modes (Off, Loop All, Loop Single).
* **Persistent Configuration**:
  * Automatically saves the last used SoundFont and directory in `~/.config/midiplayer/config.json`.

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

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/midiplayer-term.git
   cd midiplayer-term
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

---

## Keyboard Shortcuts

| Key | Action |
| :--- | :--- |
| `SPACE` | Pause / Resume playback |
| `←` / `→` | Skip backward / forward by 5 seconds |
| `N` | Skip to the next track |
| `B` | Go back to the previous track |
| `R` | Restart the current track from the beginning |
| `↑` / `↓` | Navigate the playlist (visual selection) |
| `ENTER` | Start playing the selected track in the playlist |
| `+` / `-` | Increase / decrease volume (Gain) |
| `<` / `>` | Slow down / speed up playback (0.25x ... 3.00x) |
| `L` | Toggle Loop mode (`Off` -> `All` -> `Single`) |
| `S` | Enable / disable random playback (`Shuffle`) |
| `1` – `9` | Mute / unmute MIDI channels 1 to 9 |
| `0` | Mute / unmute MIDI channel 10 (Percussion / Drums) |
| `F1` – `F6` | Mute / unmute MIDI channels 11 to 16 |
| `Q` | Quit the program |

---

## Command Line Options

```
Usage: midiplayer.py [-h] [--dir DIR] [--soundfont SOUNDFONT]
                     [--gain GAIN]
                     [--shuffle] [--loop {off,all,single}]
                     [files ...]

Positional arguments:
  files                 .mid/.midi files to add to the playlist

General options:
  --dir DIR             Directory containing .mid/.midi files
  --soundfont SOUNDFONT Path to the SoundFont file (.sf2)
  --gain GAIN           Initial audio gain (default: 0.5)
  --shuffle             Start the playlist in shuffle mode
  --loop {off,all,single}
                        Initial loop mode (default: off)
```

---

## Legacy CLI Version

In the releases section of this repository, you can find older versions of this tool that utilize the `fluidsynth` CLI executable as an external process (via `subprocess`). Please note that this legacy version is now considered obsolete, is no longer supported, and will not receive any future updates.

---

## Configuration File

Parameters entered in interactive mode are saved in the JSON configuration file:
`~/.config/midiplayer/config.json`

Example file structure:
```json
{
  "soundfont": "/usr/share/sounds/sf2/FluidR3_GM.sf2",
  "midi_path": "/home/user/Music/MIDI"
}
```

---

## License

Distributed under the GNU Lesser General Public License v3.0 (LGPLv3).
See `LICENSE` and `LICENSE.LESSER` for details.
