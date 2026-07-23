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
