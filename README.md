# Proxon FWT – Home-Assistant-Integration (LAN, ohne Cloud)

Steuert eine Proxon-FWT-Lüftungsanlage (Hauptplatine FW 7.4, T300 FW 3.5) direkt über die
LAN-Schnittstelle der Anlage (uNabto, UDP 5570). Die Hersteller-Cloud und die Original-App
werden nicht benötigt.

*English:* Home Assistant integration for Proxon FWT ventilation units (with T300 hot-water module),
controlling the unit directly over the LAN (uNabto, UDP 5570) – no manufacturer cloud required.
Set-up only needs the device ID and password from the access card; room controllers are mapped
to areas. Verified writes with automatic retries, weekly schedule editing, holiday mode.
Entity names are available in German and English; this README is in German.

Version 2.7.0 (06.10.2026). Grundlage: eine per Nabto-Mitschnitt verifizierte Adress-Tabelle
sowie Read-only- und Schreibtests gegen eine reale Anlage. Getestet mit Home Assistant 2026.9.

## Rechtliches und Haftung

- Inoffizielles Community-Projekt ohne Verbindung zu Proxon/Walter Meier oder Nabto. „Proxon" und
  „FWT" sind Marken der jeweiligen Inhaber und werden nur zur Beschreibung der Kompatibilität genannt.
- Nutzung auf eigene Verantwortung (siehe LICENSE). Schreibzugriffe verändern Einstellungen der
  Anlage; die Integration prüft Firmware, Fehlerwörter und Betriebszustände vor jedem Schreibvorgang,
  ersetzt aber keine Fachkenntnis. Prüfe, ob Eingriffe über die LAN-Schnittstelle Auswirkungen auf
  Garantie oder Wartungsverträge haben.
- Das Herstellerportal wird nur einmalig zum Signieren des Client-Zertifikats mit den eigenen
  Zugangsdaten kontaktiert – wie die Original-App. Es werden keine Zugangsdaten gespeichert oder
  weitergegeben. Ändert der Hersteller diesen Dienst, ist der manuelle Zertifikat-Fallback zu nutzen.
- Die Protokollinformationen wurden ausschließlich zum Zweck der Interoperabilität ermittelt
  (§ 69e UrhG); das Repository enthält keinen Code des Herstellers oder von Nabto.

## Installation

**Über HACS (empfohlen)**

1. HACS → Integrationen → Menü (⋮) → *Benutzerdefinierte Repositories* →
   `https://github.com/Fummy1990/hacs-proxon-fwt`, Typ *Integration* → Hinzufügen.
2. „Proxon FWT (LAN)" in HACS suchen → Herunterladen → Home Assistant neu starten.

**Manuell**

1. Ordner `custom_components/proxon_fwt` in das HA-Konfigurationsverzeichnis kopieren.
2. Home Assistant neu starten.

**Einrichten**

Einstellungen → Geräte & Dienste → Integration hinzufügen → „Proxon FWT (LAN)" → Geräte-ID und
Passwort von der Zugangskarte eingeben (die IP der Anlage wird im Heimnetz automatisch gefunden).
Im zweiten Schritt werden die erkannten Raumregler angezeigt und können direkt HA-Bereichen
zugeordnet werden.

Voraussetzungen: Home Assistant ≥ 2025.6, Anlage und HA im selben Netz (UDP 5570 zur Anlage),
einmalig Internetzugang zum Signieren des Zertifikats.

## Einrichtung (Config-Flow)

| Feld | Bedeutung |
| --- | --- |
| Geräte-ID | 12-stellige ID von der Zugangskarte des Herstellers. Daraus wird die Nabto-Identität `user-<ID>@phc.proxon.de` gebildet. |
| Passwort | Passwort von der Zugangskarte. Wird **einmalig** verwendet, um das Zertifikat beim Portal zu signieren, und nicht gespeichert. |
| IP-Adresse / Port | Anlage im Heimnetz; wird per UDP-Broadcast vorgeschlagen. |
| Client-Zertifikat (PEM, optional) | Nur für den Ausnahmefall, dass bereits ein Zertifikat vorliegt. Normalerweise leer lassen – die Integration holt es selbst. |

Die Integration besorgt das Zertifikat selbst. Der Nutzer gibt nur **Geräte-ID + Passwort** ein; im
zweiten Schritt werden die erkannten Raumregler HA-Bereichen zugeordnet.

### Woher kommt das Client-Zertifikat?

Die Anlage akzeptiert nur Client-Zertifikate, die über das Herstellerportal signiert wurden; im
Handshake wird deren gekürzter SHA-256-Fingerprint geprüft. Die Integration erzeugt bei der
Einrichtung lokal ein Schlüsselpaar und lässt das Zertifikat **einmalig** mit Geräte-ID und
Passwort vom Portal signieren – derselbe Vorgang, den die Original-App bei der ersten Anmeldung
ausführt. Danach erfolgt jede Steuerung direkt über LAN; das Portal wird nicht mehr kontaktiert.
Jede Neu-Einrichtung registriert ein weiteres Zertifikat (wie ein erneuter App-Login).

**Manueller Fallback.** Liegt bereits ein Zertifikat vor (z. B. aus dem Nabto-Profilordner der App,
`users/user-<Geräte-ID>_at_phc.proxon.de.crt`), kann sein PEM-Inhalt im Feld „Client-Zertifikat"
eingefügt werden; dann wird das Portal nicht kontaktiert.

Einrichtungen der Version 0.1 werden migriert, müssen aber neu eingerichtet werden (Geräte-ID +
Passwort), da sie kein Zertifikat enthalten.

## Optionen

Einstellungen → Integration → „Konfigurieren":

- **Abfrageintervall** (15–600 s, Standard 30 s). Jede Abfrage ist eine eigene Nabto-Sitzung
  mit ca. 20 Lese-RPCs (≈ 6 s).
- **Raum → Bereich**: Jeder verbundene Raumregler ist ein eigenes HA-Gerät („Proxon <Raumname>").
  Hier wird ihm ein HA-Bereich zugeordnet (alternativ direkt im Gerätedialog).

## Entities

**Pro Raum (Gerät „Proxon <Raumname>")**

- `climate.proxon_<raum>` – Solltemperatur (Wohnzimmer 16–24 °C in 0,5 K; Nebenräume
  Referenz ±3 K in 1 K), Ist-Temperatur. HVAC-Modus **Auto** = Anlage entscheidet (Wärmepumpe/Lüftung),
  **Heizen** = Elektroheizung (PTC) für diesen Raum freigegeben – dieselbe Funktion wie der PTC-Schalter,
  mit denselben Prüfregeln. `hvac_action`: PTC-Relais aktiv → heating, sonst „Aktueller Betrieb" der
  Anlage (Heizen/Kühlen), sonst idle.
- `switch.proxon_<raum>_elektroheizung_ptc` – PTC-Freigabe des Raums. Die Freigabe des Wohnzimmers ist
  zugleich die globale Freigabe des Hauptpanels (ZBP) und zusätzlich auf dem Hauptgerät als
  „Elektroheizung Freigabe (ZBP)" vorhanden.
- `sensor.proxon_<raum>_temperatur` – Ist-Temperatur (für Verlauf).

**Gerät „Proxon Zeitprogramm"** (Wochenprogramm Lüftung, 7 Tage × 3 Phasen)

- Übersicht: `sensor.proxon_zeitprogramm_<wochentag>` (7 Sensoren, Montag … Sonntag) zeigen den Tag in
  einer Zeile, z. B. `06:00–08:00 Stufe 2 · 12:00–13:00 Stufe 1 · 17:00–22:00 Stufe 3` („Aus" =
  Phase inaktiv). Attribute `phase_1..3` mit `start`, `end`, `level`, `active`.
- Je Wochentag ein Untergerät **„Proxon Zeitprogramm Montag"** … **„Sonntag"** mit genau 9
  Einstellungen, sortiert nach Phase:
  - `time.proxon_zeitprogramm_<wochentag>_phase_<n>_beginn` / `…_ende` – Phasenzeiten.
  - `select.proxon_zeitprogramm_<wochentag>_phase_<n>_lufterstufe` – Lüfterstufe (Aus = Phase inaktiv).
- Kopieren: Buttons „Montag → Di–Fr übernehmen" und „Montag → alle Tage übernehmen" auf dem Gerät
  „Proxon Zeitprogramm"; frei wählbar über den Dienst `proxon_fwt.copy_schedule_day` (Quelltag,
  Zieltage). Es gelten dieselben Regeln wie beim Bearbeiten – ein Tag mit gerade laufender Phase wird
  gemeldet und übersprungen, die übrigen Tage werden geschrieben (eine Sitzung je Tag).
- Fertige Dashboard-Karte (ganze Woche auf einen Blick): siehe `lovelace/zeitprogramm.yaml`.
- Regeln (an der Anlage verifiziert): Ende > Start (keine Übernacht-Phasen), keine Überlappung, kein gleicher
  Minutenwert zwischen Phasen; eine laufende Phase darf nur in der Stufe geändert werden (≥ 10 min
  Abstand zu den Grenzen); Zeitänderungen nur für Phasen, die laut Geräteuhr ≥ 2 h in der Zukunft
  beginnen. Alle Feld-Writes einer Änderung laufen in einer Sitzung mit Einzelrücklesung.
- `binary_sensor.proxon_fwt_zeitprogramm_luftung_aktiv` – globaler Programmschalter (nur lesen).

**Gerät „Proxon FWT"**

- Select: Betriebsart (Aus, Sommer, Winter, ECO Komfort, Ofenbetrieb), Lüfterstufe (1–4).
- Switch: Kühlfreigabe, Intensivlüftung, E-Heizstab-Freigabe, Legionellenfunktion (standardmäßig deaktiviert, experimentell).
- Switch: Warmwasserbereitung (T300-Betriebsart, App „T300Betriebsart"); **Urlaubsmodus** – führt die
  Nebenwrites des App-Urlaubsmodus aus (Lüfterstufe 1, Wohnzimmer/Zone 2 18 °C, alle Raum-Offsets −3 K,
  alle Raum-PTC aus, Warmwasserbereitung aus) und stellt beim Ausschalten die gesicherten Werte wieder her
  (Warmwasser ein, nach 2 s Heizstab-Freigabe, dann der Rest). Die rohe Betriebsart 5 wird bewusst nicht
  geschrieben (unbekannte Reglerwirkung; vom Hersteller in App 1.7.4 deaktiviert).
- Number: Intensivlüftung Dauer (min), Warmwasser-Solltemperatur (40–55 °C), E-Heizstab-Solltemperatur (40–55 °C),
  Zone 2 Solltemperatur (16–24 °C), Grenzwert CO₂ (ppm), Grenzwert Feuchte (%).
- Sensoren: T1 Zuluft, T3 Frischluft, T4 Fortluft, T7 Abluft, Zone 2 Temperatur, T12/T13/T14 (Diagnose),
  P14 ND Verdampfer (bar, Diagnose), P19 Druckdifferenz Abtau (Skalierung unsicher, deaktiviert), Drehzahlen Zu-/Abluft, Lüfterstufe Ist, Luftfeuchte Abluft,
  Kompressor-Drehzahl, Vierwegeventil, Schieberposition, E-Ventil-Positionen, Status Kompressor/Ventilatoren,
  Intensivlüftung Restzeit, Warmwasser Mitte/Unten, T300-Temperaturen, **Aktueller Betrieb**
  (Lüftung/Heizen/Kühlen), Betriebsstunden und Filter Laufzeit (die Anlage zählt in 2-Stunden-Schritten –
  wird umgerechnet), Filter Standzeit (Monate), **Filter Restlaufzeit** (Tage, berechnet aus Standzeit −
  Laufzeit), T300 Filterwechselintervall (deaktiviert), Heizmodul 1/2 aktive Relais (Bitmaske R1–R10 als
  Attribut), Firmware, Geräteuhr, Uhrabweichung (Geräteuhr − HA-Zeit), Fehlerwörter (Diagnose).
- Binärsensoren: Störung, Bypass, Erdwärme, Magnetventil, PTC-Relais aktiv, E-Heizstab aktiv, T300-Relais,
  PV-Signale, CO₂-Sensor vorhanden, Bedienpanel gesperrt (nur lesen – der Sperrcode des Panels lässt sich
  über LAN nicht setzen).

Quellen der Zuordnung: die verifizierte Adresstabelle (Steuerfunktionen, Räume, Zeitprogramm-Formel) und das
extrahierte Modell der Original-App (54 Datenpunkte/Sollwerte mit Nabto-Adressen). Nicht über LAN
möglich (Schreibversuche bleiben ohne Antwort bzw. `NO_ACCESS` – nicht in der Nabto-Schreibtabelle der
Anlage): PV-Freigabe, E1/E2-Boostdauer, Raumnamen, Geräteuhr (Zeitsync), Bedienpanel-Sperre. Diese Werte setzt nur das
Service-Tool per Modbus-RTU am Gerät.

## Schreibweg (Sicherheitskonzept)

Jeder Schreibvorgang läuft in einer frischen Nabto-Sitzung:

1. Guard: Firmware (74 bzw. 35) und neun Fehlerwörter lesen; je nach Funktion zusätzliche
   Bedingungen (PTC ein nur in Sommer/Winter ohne Fehler, Kühlfreigabe nicht bei aktiven
   PTC-Relais, Heizstab nur im T300-Ruhezustand, Betriebsartwechsel nicht bei Intensivlüftung).
2. Aktuellen Wert über den Lesepunkt lesen; identischer Wert → kein Write.
3. Genau ein Query-43-Write (ein Wert je RPC) auf den getrennten Schreib-Alias; ACK-Status muss 1 sein.
4. Rücklesung des Lesepunkts im Polling bis zur Maximalwartezeit (3 s Sollwerte/Lüfter, 5 s
   Betriebsart/PTC/Kühlung/Boost, 60 s Warmwasser/Heizstab, 90 s T300-Betriebsart).
5. Bleibt die Antwort aus, ist der Status ≠ 1 (z. B. 98 = T300 beschäftigt) oder wird der Zielwert
   nicht sichtbar, wird der identische Write automatisch bis zu **2× wiederholt** (idempotent).
   Danach eine Bestätigungslesung; Abweichung → Fehler in HA und Eintrag im Diagnose-Sensor
   „Letzter Schreibfehler" (mit Zeitstempel und Zähler).
6. Sitzung mit Close-Frame beenden.

PTC **aus**schalten ist auch bei Fehlerbits erlaubt.

## Adress-Referenz (Auszug)

| Funktion | Lesepunkt | Schreib-Alias (Q43) | Skalierung |
| --- | --- | --- | --- |
| Betriebsart | SP0:16 | 0:42 | 0–4 |
| Lüfterstufe | SP0:22 | 0:54 | 1–4 |
| Kühlfreigabe | SP0:62 | 0:134 | 0/1 |
| Wohnzimmer Soll | SP0:70 | 0:150 | °C×100 |
| Nebenraum i Offset | SP1:(2+i) | 0:(434+2i) | K, Soll = SP1:(22+i)+Offset |
| Wohnzimmer PTC | SP0:187 | 0:384 | 0/1 |
| Nebenraum i PTC | SP1:(42+i) | 0:(514+2i) | 0/1 |
| Intensivlüftung | SP0:185 | 0:380 | 0/1 |
| Intensivlüftung Dauer | SP0:186 | 0:382 | min |
| Warmwasser Soll | SP7:1 | 1:0 | °C×10 |
| E-Heizstab Freigabe / Soll | SP7:2 / SP7:4 | 1:1 / 1:3 | 0/1, °C×10 |
| Raum-Ist Wohnzimmer / Nebenraum i | DP1:113 / DP2:(17+3i) | – | /100 bzw. /10 °C |
| Verbindungsmaske | SP1:0, SP1:1 | – | NBE2..20 = Bit 0..18, NBE1 = Bit 19 |
| Raumnamen | SP6:(10i..10i+9) | – | 2 Zeichen/Wort, latin-1 |

## Qualitätssicherung

hassfest- und HACS-Validierung laufen bei jedem Push (GitHub Actions). Die Integration wird vor
jedem Release gegen eine reale Anlage sowie mit einer Unit-Test-Suite (HA-Testharness, lokal) geprüft.

## Übersetzungen

Entity-Namen kommen aus `translations/de.json` und `translations/en.json` (Übersetzungsschlüssel je
Entity; Raum- und Zeitprogramm-Entities mit Platzhaltern). Entity-IDs bestehender Entities bleiben
beim Wechsel der Sprache erhalten.

## Hinweise

- Während Schreibvorgängen sollte die Original-App geschlossen sein (parallele Sitzungen).
- Die Zuordnungen gelten für Firmware 7.4 / 3.5; bei anderer Firmware werden Writes gesperrt.
- Nur im eigenen, vertrauenswürdigen Heimnetz betreiben; Nabto-Verkehr ist unverschlüsselt (NULL-Crypt).
