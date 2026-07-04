# Open Duck Mini – Bedienungsanleitung

Diese Anleitung beschreibt alle Betriebsarten des Roboters, die Belegung des
Xbox-Gamepads (je Modus) sowie die Bedienung über die Web-Oberfläche am Handy.

> **Wichtig zum Ausdrucken:** Der Roboter kennt **zwei Programme** (Lauf-Modus und
> Kopf-Puppet-Modus). Sie werden getrennt gestartet. Das Gamepad ist in jedem
> Programm **unterschiedlich** belegt – die beiden großen Tabellen weiter unten
> zeigen genau, welche Taste wo was macht.

---

## Überblick der Betriebsarten

| Programm | Was es macht | Start |
|---|---|---|
| **Lauf-Modus** | Der Roboter läuft (KI-Laufregler). Fahren, Sprinten, Tempo, Aufnahme, Kopf steuern. | `v2_rl_walk_mujoco.py` |
| **Kopf-Puppet-Modus** | Beine bleiben stehen, der **Kopf** wird gesteuert. Enthält **Idle-Aufnahme** und **Gesichts-Tracking**. | `head_puppet.py` |

Beide Programme bieten zusätzlich eine **Web-Oberfläche** fürs Handy (siehe Abschnitt 3).

### Roboter starten (Terminal auf dem Roboter)

```bash
cd ~/Open_Duck_Mini_Runtime/scripts

# Lauf-Modus (Pfad zum Modell ist Pflicht):
python v2_rl_walk_mujoco.py --onnx_model_path <pfad>/BEST_WALK_ONNX_2.onnx

# Kopf-Puppet-Modus:
python head_puppet.py
```

Beim Start fährt der Roboter zuerst in seine Grundstellung (kurz stillhalten lassen).
Im Lauf-Modus startet er standardmäßig **pausiert** – mit **A** entpausieren.

---

## 1. Lauf-Modus (Walk)

### Fahren (linker + rechter Stick)
- **Linker Stick hoch/runter** → vorwärts / rückwärts
- **Linker Stick links/rechts** → seitwärts (seitliches Versetzen)
- **Rechter Stick links/rechts** → drehen (auf der Stelle / in der Kurve)

### Tempo / Schrittfrequenz – **D-Pad ↑ / ↓**
Das **D-Pad hoch/runter** verändert die **Schrittfrequenz** (wie schnell die Beine
takten), in Schritten von ±0,05:
- **D-Pad ↑** → Takt schneller (zügigeres Gehen)
- **D-Pad ↓** → Takt langsamer (**ruhiger und stabiler**)

> Langsamerer Takt = deutlich standfester. Der aktuelle Wert wird in der Web-App
> unter **GAIT** angezeigt.

### Sprint – **LB (halten)**
**LB gedrückt halten** → schnellerer Gang (Sprint), solange gehalten.

### Kopf steuern – **Y**
**Y** schaltet den **Kopf-Steuermodus** um (experimentell): Danach steuern die
Sticks den **Kopf** (Nicken / Gieren / Rollen) statt zu fahren. Erneut **Y** =
zurück zum Fahren.

### Aufnahme & Wiedergabe eines Laufs
Der Roboter kann eine gefahrene Sequenz aufnehmen und in Schleife abspielen.

> **So funktioniert das Starten der Aufnahme zuverlässig:**
> 1. **A** drücken → Roboter **pausieren**.
> 2. **D-Pad Links ~3 Sekunden halten** → Aufnahme startet (Ton *happy1*).
> 3. **A** drücken → entpausieren und **losfahren**. Alles wird aufgezeichnet.
> 4. **D-Pad Links kurz antippen** → Aufnahme **beenden** (Ton *beep2*). Das Beenden
>    funktioniert auch **während des Laufens**. Die Aufnahme wird gespeichert
>    (`walk_recording.pkl`) und bleibt auch nach einem Neustart erhalten.
>
> **Wiedergabe:** **D-Pad Rechts** antippen → Aufnahme läuft in Schleife (Ton *beep1*).
> Stoppen: **D-Pad Rechts** erneut, oder einfach **einen Stick bewegen** (übernimmt
> sofort wieder die Live-Steuerung).

### Balance-Feinjustage (IMU-Trim) – **RB + D-Pad**
Für die Balance-Kalibrierung während des Laufens **RB gedrückt halten**; das D-Pad
justiert dann die Neigungskorrektur (statt Tempo/Aufnahme):
- **RB + D-Pad ↑ / ↓** → Nick-Trim (vorne/hinten)
- **RB + D-Pad ← / →** → Roll-Trim (links/rechts)
- **RB + Y** → aktuellen Trim **dauerhaft speichern**

> Solange **RB** gehalten wird, sind Tempo (D-Pad ↑/↓) und Aufnahme (D-Pad ←/→)
> **deaktiviert**, damit nichts doppelt auslöst.

### Weitere Tasten
- **A** → Pause / Weiter (der Roboter pausiert auch **automatisch bei einem Sturz** –
  danach aufrichten und **A** zum Fortsetzen).
- **X** → Projektor / „Scanner-LED" ein/aus.
- **B** → zufälliger Sound.
- **LT / RT** → Antennen bewegen.

### 📋 Gamepad-Belegung – Lauf-Modus

| Taste / Kombination | Funktion |
|---|---|
| Linker Stick ↑↓ | vorwärts / rückwärts |
| Linker Stick ←→ | seitwärts |
| Rechter Stick ←→ | drehen |
| **A** | Pause / Weiter (auch nach Sturz) |
| **X** | Projektor (Scanner-LED) ein/aus |
| **B** | zufälliger Sound |
| **Y** | Kopf-Steuermodus umschalten |
| **LB** (halten) | Sprint (schnellerer Gang) |
| **LT / RT** | Antennen |
| **D-Pad ↑ / ↓** | Schrittfrequenz schneller / langsamer (±0,05) |
| **D-Pad ←** (3 s halten) | Aufnahme starten *(Roboter vorher pausieren)* |
| **D-Pad ←** (kurz) | Aufnahme beenden |
| **D-Pad →** | Wiedergabe starten / stoppen |
| **RB + D-Pad ↑ / ↓** | IMU-Trim: Nicken |
| **RB + D-Pad ← / →** | IMU-Trim: Rollen |
| **RB + Y** | IMU-Trim speichern |

---

## 2. Kopf-Puppet-Modus (Head-Puppet)

Die Beine bleiben in Grundstellung stehen – gesteuert wird nur der **Kopf**.

### Kopf & Antennen
- **Linker Stick ←→** → Kopf **gieren** (nach links/rechts drehen)
- **Linker Stick ↑↓** → Kopf **nicken** (hoch/runter)
- **Rechter Stick ←→** → Kopf **rollen** (neigen)
- **LT / RT** → Antennen
- **B** → zufälliger Sound
- **X** → Projektor / Scanner-LED ein/aus

### Idle-Aufnahme (eigene Bewegungsschleife)
- **D-Pad Links ~3 s halten** → Aufnahme startet (Ton *happy1*). Aufgezeichnet werden
  **Kopfbewegung, Antennen, Sounds und Projektor**. Automatischer Stopp nach **60 s**,
  oder **D-Pad Links kurz antippen** zum Beenden (Ton *beep2*).
- **D-Pad Rechts** → gespeicherte Schleife **endlos abspielen** (Ton *beep1*).
  Stoppen: **D-Pad Rechts** erneut, oder **irgendeinen Stick / Trigger / Knopf** berühren.

### Gesichts-Tracking – **D-Pad ↑**
- **D-Pad ↑** → Gesichts-Tracking: Der Kopf folgt dem **nächstgelegenen Gesicht**
  (Kamera + OpenCV).
  - Wird ein Gesicht **1,5 s** gehalten, **begrüßt** der Roboter (Antennen-Wackeln +
    Ton *happy2* + ca. 5 s Scanner-Scan). Beim **Verlieren** des Gesichts wackelt er
    zum Abschied.
  - Ist **kein Gesicht** zu sehen, spielt er deine **aufgenommene Idle-Schleife** ab.
  - **Beenden:** **D-Pad ↑** erneut, oder einen **Stick** bewegen.
- Voraussetzung: In der Konfiguration `camera: true` und eine angeschlossene Kamera.
  Ohne Kamera ist die Funktion einfach deaktiviert (kein Absturz).

### 📋 Gamepad-Belegung – Kopf-Puppet-Modus

| Taste | Funktion |
|---|---|
| Linker Stick ←→ | Kopf gieren (drehen) |
| Linker Stick ↑↓ | Kopf nicken |
| Rechter Stick ←→ | Kopf rollen (neigen) |
| **LT / RT** | Antennen |
| **B** | zufälliger Sound |
| **X** | Projektor (Scanner-LED) ein/aus |
| **D-Pad ↑** | Gesichts-Tracking ein/aus |
| **D-Pad ←** (3 s halten) | Idle-Aufnahme starten |
| **D-Pad ←** (kurz) | Aufnahme beenden |
| **D-Pad →** | Idle-Schleife abspielen / stoppen |

> Im Kopf-Puppet-Modus gibt es **kein** Pause (A), **kein** Sprint (LB) und
> **kein** Tempo (D-Pad ↑/↓ ist hier für Tracking belegt).

---

## 3. Web-Oberfläche (Handy / Browser)

Beide Programme starten zusätzlich eine kleine Web-App, mit der man den Roboter
**vom Handy** steuern kann – mit Bildschirm-Joysticks und -Knöpfen, plus Anzeige von
Neigung, Akku, Temperatur usw.

### Verbinden (kein IP-Tippen nötig)
1. Am Handy mit dem WLAN des Roboters (**„Openduck"**) verbinden. Die Verbindung
   **bleibt bestehen** (keine störende „Anmelden"-Popup mehr).
2. Im Browser **irgendeine `http://`-Adresse** öffnen – man wird automatisch zur
   Steuerseite weitergeleitet. Alternativ direkt **`http://10.42.0.1`** öffnen.
3. Tipp: Über **„Zum Home-Bildschirm"** eine Verknüpfung anlegen → 1-Tipp-Zugriff.

> Hinweis: Es funktioniert nur mit **`http://`** (nicht `https://`). Der Roboter hat
> kein Internet – das ist normal.

Die Web-App erkennt automatisch, ob gerade der **Lauf-** oder der **Kopf-Puppet-Modus**
läuft, und zeigt die passenden Bedienelemente.

### Anzeige (beide Modi)
Oben: künstlicher Horizont (Nick/Roll), Akku (Volt/Prozent), Schleifen-Rate (loop Hz),
Temperatur, Aufnahme-Status (rec), Laufzeit. Im Lauf-Modus zusätzlich **GOV**
(Stabilitäts-Regler), falls aktiviert.

### Lauf-Modus in der Web-App
- **DRIVE** (linker Screen-Joystick) = fahren, **TURN** (rechter) = drehen
- Knöpfe: **Y** (Kopf), **X** (Scanner), **B** (Sound), **A** (Pause)
- D-Pad: **GAIT+ / GAIT−** (Tempo), **REC** (halten = Aufnahme), **PLAY** (Wiedergabe)
- **LB** (halten = Sprint)
- **IMU-Trim-Karte** (Pitch/Roll ± und **SAVE**) zur Balance-Feinjustage
- Antennen-Schieberegler

### Kopf-Puppet-Modus in der Web-App
- Screen-Joysticks steuern den **Kopf**
- Knöpfe: **X** (Scanner), **B** (Sound)
- D-Pad: **TRACK** (Gesichts-Tracking), **REC** (halten = Aufnahme), **PLAY** (Wiedergabe)

> Gamepad und Web-App laufen **gleichzeitig** – beide steuern denselben Roboter.

---

## Akustische Rückmeldung (Töne)

Wenn ein Lautsprecher aktiviert ist, quittiert der Roboter Aufnahme/Wiedergabe:

| Ereignis | Ton |
|---|---|
| Aufnahme **gestartet** | `happy1` (fröhliches Zwitschern) |
| Aufnahme **beendet** / voll | `beep2` |
| Wiedergabe **gestartet** | `beep1` |
| Wiedergabe **gestoppt** (manuell) | *(kein Ton)* |
| Sturz erkannt | `beep2` |

---

## Schnellübersicht (Cheat-Sheet)

**Lauf-Modus:** Sticks = fahren/drehen · **A** = Pause · **LB** = Sprint ·
**D-Pad ↑↓** = Tempo · **D-Pad ←** (3 s) = Aufnahme (vorher pausieren) ·
**D-Pad →** = Wiedergabe · **Y** = Kopf · **X** = Scanner · **B** = Sound ·
**RB + D-Pad / Y** = Balance-Trim.

**Kopf-Puppet-Modus:** Sticks = Kopf · **D-Pad ↑** = Gesichts-Tracking ·
**D-Pad ←** (3 s) = Idle-Aufnahme · **D-Pad →** = Wiedergabe · **X** = Scanner ·
**B** = Sound · **LT/RT** = Antennen.

**Web-App:** „Openduck"-WLAN → `http://10.42.0.1` (oder beliebige http-Seite) →
zum Home-Bildschirm hinzufügen.
