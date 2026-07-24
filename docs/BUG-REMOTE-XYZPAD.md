# Bug SPAT Revolution Remote — crash `get(...)[i] is undefined` (xyzpad)

## Symptôme

Au démarrage de la **SPAT Revolution Remote** (surface Open Stage Control), la console
Electron / navigateur crache en boucle, à chaque source poussée par SPAT :

```
xy_Dual2.onValue javascript error: TypeError: can't access property 2, get(...) is undefined
fader_srcDual2_z.onValue javascript error: TypeError: can't access property 0, get(...) is undefined
xy_Dual1.onValue  ... property 2 ...
fader_srcDual1_z.onValue ... property 0 ...
```

Stack type :

```
setValue xy.mjs / slider.mjs
  ← changed widget.mjs:343
    ← trigger event-emitter.mjs:41
      ← setValue variable.mjs:36
        ← receive osc.mjs:80   (SPAT pousse une position au demarrage)
```

## Cause racine

Les scripts `onValue` des widgets `xy_Dual1/2`, `fader_srcDual1/2_z` et `multixy_1` lisent la
position courante via `get("variable_xyzpad")` puis l'indexent (`[0]`, `[2]`).

`variable_xyzpad` a `default: ""`. Tant que ce widget n'a pas reçu de valeur (c.-à-d. avant la
première position de la source sélectionnée par `dropdown_srcSelMain`), `get("variable_xyzpad")`
renvoie `undefined`. Or au démarrage SPAT pousse en OSC les positions **de toutes les sources**,
donc les scripts des pads Dual/multixy tournent alors que `variable_xyzpad` est encore vide →
`undefined[2]` jette. C'est une **course à l'initialisation**, pas un bug d'Open Stage Control
(le binaire, 1.30.4, est sain).

Le fichier fautif est la **session** chargée par le serveur OSC :

```
/Library/Application Support/Flux/SPAT Remote Server/SPATRevolutionRemote.json   (session OSC 1.29.3)
```

pointée par la clé `load` de `SPAT_Remote_Config_macOS.config`. Le dépôt public
`FLUX-SE/SPATRevolutionRemote` est figé en 2022 (session OSC **1.17.0**) et ne contient même pas
ces widgets : il ne peut pas servir de source du correctif.

## Les trois classes de bugs corrigées

1. **Crash (`undefined[i]`)** — 7 lectures `get("variable_xyzpad")[i]` non gardées.
   Corrigé par un fallback : `(get("variable_xyzpad") || [0,0,0])[i]`. Aucun changement de
   comportement une fois la variable peuplée ; supprime toutes les `TypeError` au démarrage.

2. **Y écrasé par X** (`fader_srcDual1_z`, `fader_srcDual2_z`) — le fader Z réinjectait
   `set("variable_xyzpad", [xyz[0], xyz[0], value])`, soit `[X, X, Z]` : bouger le Z d'un pad
   Dual réinitialisait le **Y** de la source à la valeur du X. Corrigé en `[xyz[0], xyz[1], value]`.

3. **`value[2]` inexistant** (`xy_Dual1`, `xy_Dual2`) — quand les deux pads Dual pointent la même
   source, le pad courant recopiait `value[2]` vers le fader Z jumeau. Or la valeur d'un pad XY
   est `[x, y]` : `value[2]` est `undefined` et écrasait le Z. Corrigé en lisant le vrai Z du
   fader du pad courant (`get("fader_srcDual1_z")` / `get("fader_srcDual2_z")`).

## Bug de synchro inter-navigateurs (onglet main non mis à jour)

**Symptôme** — Bouger une source depuis l'onglet **Multi-Source** (ou un pad **Dual**) la déplace
bien dans SPAT, mais l'onglet **Main** d'un *autre* navigateur connecté ne suit pas — ni l'onglet
**Source**. L'inverse (Main/Source → Multi) fonctionne.

**Cause** — Un widget ne met à jour ses homologues (autre client, ou feedback moteur) que si le
message émis correspond à une **adresse écoutée**. Les récepteurs de position de la session
écoutent tous `/source/N/xyz` : `variable_xyzpad` (Main), `variable_xyMulti1..8` (Multi),
`variable_xyDual1/2` (Dual). Or `xy_main` et les faders `spat_position*` émettent bien sur `/xyz`
(et `/x`,`/y`,`/z`), tandis que `multixy_1` et `xy_Dual1/2` émettaient sur `/source/N/`**`xy`** —
adresse qu'**aucun widget n'écoute**. D'où l'asymétrie.

**Correctif** — Faire émettre `multixy_1` et `xy_Dual1/2` sur `/source/N/xyz` (avec le z courant
du point : `variable_xyMultiN` pour un slot Multi, le fader `fader_srcDualN_z` pour un Dual), au
lieu de `/xy`. Le message est alors capté directement par les récepteurs `/xyz` sur **tous** les
clients — exactement comme le fait déjà le pad Main. Les `onTouch` continuent d'émettre `'touch'`/
`'release'` sur `/xy` (signalisation de geste vers SPAT, identique au pad Main). Les 5 ponts
locaux `set("variable_xyzpad", …, {send:false})` sont **laissés tels quels** : ils ne servent qu'à
mettre à jour l'écran du client local.

**Piège écarté — le ping-pong SPAT.** Tenter de synchroniser en activant le *Ping-pong* de la
sortie SPAT casse tout : capture OSC à l'appui, SPAT renvoie alors les marqueurs `'touch'`/
`'release'` sous forme de **chaîne** sur `/source/N/xyz` et `/aed` :

```text
SPAT->Remote | /source/3/xyz | ,s | 'touch'      ← chaîne, pas des floats
SPAT->Remote | /source/3/xyz | ,fff | [0.48, 1.0, 0.0]
```

Les widgets `variable` acceptent **n'importe quel type sans contrôle** (`this.value = a`), donc la
chaîne `'touch'` est stockée dans `variable_xyzpad` → le pad reçoit du non-numérique → il se clampe
à **(-1, -1)**, systématiquement. Le correctif ci-dessus rend le ping-pong inutile ; il doit rester
**désactivé**. (Les faders, eux, ignorent silencieusement les valeurs non numériques — seules les
`variable` sont vulnérables.)

## Bug : deux clients sur le même Main, même source → l'un décroche

**Symptôme** — Les deux fenêtres sur l'onglet **Main**. Dès qu'elles sélectionnent la **même**
source, l'autre fenêtre « bascule » sur un autre élément : le pad reste figé sur la position
initiale de la source et ne suit plus aucun mouvement.

**Cause** — `dropdown_srcSelMain` (et `switch_roomSelect` pour la Room) ont pour adresse
`/source/@{this.value}/dump`. Ils s'en servent pour **envoyer** la requête de dump dans leur
`onValue` (`send('/source/' + value + '/dump')`), mais cette adresse les fait aussi **écouter**
sur `/source/X/dump`. Le `send()` d'un script force l'émission malgré `bypass: true`, donc la
requête est **diffusée aux autres clients** (client-sync). Quand deux clients pointent la même
source X, chacun reçoit la requête de dump de l'autre sur `/source/X/dump`. Ce message est **sans
argument** → `Osc.receive` applique `setValue(null)` → la sélection du dropdown est écrasée. Le pad
Main, dont l'adresse est `/source/@{dropdown_srcSelMain}/xyz`, ne résout plus et se fige.

SPAT ne répond **jamais** sur `/dump` (le dump revient sous forme de messages par paramètre :
`/xyz`, `/aed`, `/gain`… — vérifié à la capture). Cette écoute sur `/dump` est donc inutile.

**Correctif** — Mettre l'`address` de `dropdown_srcSelMain` et `switch_roomSelect` à `auto`
(toutes les instances left/right). L'envoi du dump reste dans l'`onValue` ; seule l'écoute nuisible
sur `/dump` disparaît. Chaque client garde sa propre sélection.

## Bug : le bouton Select clignote et ne reste pas enclenché

**Symptôme** — Cliquer le bouton **Select** sur l'onglet Main le fait clignoter ; il ne reste
jamais enfoncé. (Sélectionner via le **pad** — appui/relâché — fonctionne, car les pads envoient
`send('/source/N/select', 1)` avec la valeur.)

**Cause** — `button_mainSelect.onValue` envoyait la sélection **sans argument** :
`send('/source/' + src + '/select')`. Capture OSC à l'appui, SPAT interprète un select sans
argument comme **[0]** (désélection) :

```text
Remote->SPAT | /source/1/select | ,   | []     (sans argument)
SPAT->Remote | /source/1/select | ,i  | [0]
```

Le bouton passe donc à 1 localement au clic, envoie sans valeur, SPAT répond [0], le bouton
revient à 0 → clignotement.

**Correctif** — Envoyer la valeur : `send('/source/' + src + '/select', 1)`. Le bouton reçoit
alors [1] en retour et reste enclenché.

## Note : mode « Selection » (client qui suit la source sélectionnée)

Le dropdown Main a une option **« Selection » = index -1**. Aucun script ne gère ce -1 : le mode
repose entièrement sur le feedback moteur. Il faut activer **« Send current selection messages »**
dans les réglages **généraux** du SPAT Remote server (pas dans le panneau OSC Socket). SPAT émet
alors la source sélectionnée sur l'alias `-1` : capture confirmée, `/source/-1/xyz` et
`/source/-1/aed` arrivent en **valeurs numériques** (les chaînes `'touch'` ne polluent que les
paramètres EQ, pas la position). Le mode « Selection » est donc viable une fois ce toggle activé.

## Bug : le pad tremble en mode « Selection » quand la source bouge

**Symptôme** — Un client en mode « Selection » (dropdown Main = -1) : quand la source
sélectionnée bouge (dans SPAT, une timeline, un autre client…), son pad **tremble**.

**Cause — confirmée par capture** (`minus1.pcap`, 2122 messages SPAT→Remote, **0** Remote→SPAT,
donc pas de boucle). SPAT, avec **Touch/release** actif en sortie, émet les marqueurs
`'touch'`/`'release'` comme des **chaînes** sur les adresses de position, **y compris sur l'alias
`-1`** :

```text
SPAT->Remote | /source/-1/aed | ,s   | 'touch'          <- chaîne
SPAT->Remote | /source/-1/aed | ,fff | [137.48, 0.0, …]  <- vraie position
```

Les widgets `variable` acceptent tout type sans contrôle : `variable_aedpad` / `variable_xyzpad`
stockent tour à tour la chaîne (→ clamp (-1,-1)) et la vraie position → **tremblement**. Ce n'est
pas une boucle OSC, c'est du feedback moteur normal mal filtré.

**Correctif** — Poser un **garde anti-poison** en tête de l'`onValue` des 12 récepteurs de
position (`variable_xyzpad`, `variable_aedpad`, `variable_xyMulti1..8`, `variable_xyDual1/2`) :

```js
if (!Array.isArray(value) || typeof value[0] !== 'number') return;
```

Les `'touch'`/`'release'` (et tout non-numérique) sont ignorés ; seules les positions numériques
passent. Comportement normal inchangé (les mises à jour internes passent des tableaux `[x,y,z]`).
Ce garde protège aussi définitivement de tout `'touch'` qui arriverait sur `/xyz`/`/aed` d'une
vraie source, quel que soit le réglage moteur.

## Application du correctif

Script idempotent, avec backup horodaté et vérification des motifs avant substitution :

```sh
# aperçu sans écrire
python3 tools/patch-remote.py --dry-run

# application (cible le fichier système par défaut ; sudo automatique si besoin)
python3 tools/patch-remote.py

# sur un autre fichier de session
python3 tools/patch-remote.py /chemin/vers/SPATRevolutionRemote.json
```

Le script échoue proprement (sans rien écrire) si un motif attendu a changé, et détecte une
session déjà patchée (aucun backup superflu).

## À ré-appliquer après mise à jour FLUX Center

L'installeur FLUX **réécrit** `SPATRevolutionRemote.json` (cf. `Install.json` → `VersionDate`).
Après toute mise à jour de la SPAT Revolution Remote via FLUX Center, relancer
`python3 tools/patch-remote.py` pour ré-appliquer le correctif sur la nouvelle session.
Le backup de la version précédente est conservé à côté du fichier (`*.bak-<horodatage>`).
