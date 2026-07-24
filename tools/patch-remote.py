#!/usr/bin/env python3
"""Patch de la session SPAT Revolution Remote (Open Stage Control).

Corrige le crash `TypeError: can't access property N, get(...) is undefined`
qui apparait en boucle au demarrage de la Remote, ainsi que deux bugs de
synchro adjacents dans les widgets Dual / multixy.

Cause racine : au demarrage, SPAT pousse les premieres positions OSC avant que
le widget `variable_xyzpad` ait une valeur (son default vaut "") ; les scripts
`onValue` indexent alors `get("variable_xyzpad")[i]` sur `undefined` et jettent.

Le patch :
  1. rend defensives les lectures `get("variable_xyzpad")[i]` (fallback [0,0,0]) ;
  2. corrige le Y ecrase par X dans les faders Z des Dual ([0],[0] -> [0],[1]) ;
  3. remplace le `value[2]` inexistant d'un pad XY par le vrai Z du fader jumeau ;
  4. retablit la synchro inter-navigateurs : `multixy_1` et `xy_Dual1/2` emettaient
     la position sur `/source/N/xy`, adresse qu'AUCUN widget de la session n'ecoute.
     Les recepteurs de position (`variable_xyzpad`, `variable_xyMulti1..8`,
     `variable_xyDual1/2`) ecoutent tous `/source/N/xyz`. On change donc l'emission
     en `/source/N/xyz` (avec le z courant du point) : le message est alors capte
     directement par ces recepteurs sur tous les clients, comme le fait deja le pad
     principal. Les 5 ponts locaux `set("variable_xyzpad", ..., {send:false})` sont
     laisses tels quels (mise a jour de l'ecran local uniquement).

Note : le ping-pong de la sortie SPAT NE doit PAS servir a synchroniser. Il renvoie
les marqueurs `'touch'`/`'release'` sous forme de CHAINE sur `/xyz`/`/aed`, or les
widgets `variable` acceptent tout type sans controle : la chaine empoisonne le pad,
qui se clampe a (-1, -1). Ce correctif rend le ping-pong inutile.

Idempotent : une session deja patchee est detectee, aucun backup superflu n'est
cree. Le motif attendu est verifie avant chaque substitution ; si une version
future du fichier ne le contient plus, le script echoue proprement au lieu de
corrompre le JSON.

Usage :
    python3 tools/patch-remote.py [--dry-run] [chemin/vers/SPATRevolutionRemote.json]

Sans argument, cible le fichier reellement charge par le serveur OSC :
    /Library/Application Support/Flux/SPAT Remote Server/SPATRevolutionRemote.json
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time

DEFAULT_TARGET = (
    "/Library/Application Support/Flux/SPAT Remote Server/SPATRevolutionRemote.json"
)

GUARD = '(get("variable_xyzpad") || [0,0,0])'

# Emission de position vers `/source/N/xyz` (au lieu de `/xy`, non ecoute).
# On reprend le z courant du point : pour un slot Multi via `variable_xyMultiN`,
# pour un pad Dual via son fader Z. Le pad Main emet deja sur /xyz de la meme facon.
XYZ_MULTI = (
    "send(`/source/${src}/xy`, value[i * 2], value[i * 2 + 1]);",
    "send(`/source/${src}/xyz`, value[i * 2], value[i * 2 + 1], "
    "(get('variable_xyMulti' + (i + 1)) || [0,0,0])[2]);",
)


def _xyz_dual(z_id):
    return (
        "send('/source/' + src + '/xy', value[0], value[1]);",
        "send('/source/' + src + '/xyz', value[0], value[1], (get('" + z_id + "') || 0));",
    )


# Substitutions par widget (id -> liste de couples (old, new)).
# Chaque `old` doit etre present tel quel pour que le patch s'applique ;
# chaque `new` sert aussi de marqueur d'idempotence. Les regles crash sont
# listees avant la regle d'emission /xyz.
REPLACEMENTS = {
    "xy_Dual1": [
        (
            'set("fader_srcDual2_z", value[2], {script: false, send: false})',
            'set("fader_srcDual2_z", get("fader_srcDual1_z"), {script: false, send: false})',
        ),
        (
            'get("variable_xyzpad")[2]',
            GUARD + "[2]",
        ),
        _xyz_dual("fader_srcDual1_z"),
    ],
    "xy_Dual2": [
        (
            'set("fader_srcDual1_z", value[2], {script: false, send: false})',
            'set("fader_srcDual1_z", get("fader_srcDual2_z"), {script: false, send: false})',
        ),
        (
            'get("variable_xyzpad")[2]',
            GUARD + "[2]",
        ),
        _xyz_dual("fader_srcDual2_z"),
    ],
    "fader_srcDual1_z": [
        (
            '[get("variable_xyzpad")[0], get("variable_xyzpad")[0], value]',
            "[" + GUARD + "[0], " + GUARD + "[1], value]",
        ),
    ],
    "fader_srcDual2_z": [
        (
            '[get("variable_xyzpad")[0], get("variable_xyzpad")[0], value]',
            "[" + GUARD + "[0], " + GUARD + "[1], value]",
        ),
    ],
    "multixy_1": [
        (
            'get("variable_xyzpad")[2]',
            GUARD + "[2]",
        ),
        XYZ_MULTI,
    ],
}


def iter_widgets(node):
    """Parcours recursif de l'arbre de widgets, yield chaque dict widget."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from iter_widgets(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_widgets(v)


def find_widgets(content):
    """id de widget -> dict widget (premiere occurrence)."""
    found = {}
    for w in iter_widgets(content):
        wid = w.get("id")
        if wid in REPLACEMENTS and wid not in found:
            found[wid] = w
    return found


def apply_patch(data):
    """Applique les substitutions in-place. Retourne (changes, warnings).

    changes  : liste de (widget_id, index_regle) reellement appliquees.
    warnings : liste de messages (regle deja appliquee ou motif introuvable).
    """
    content = data.get("content")
    widgets = find_widgets(content)

    missing = [wid for wid in REPLACEMENTS if wid not in widgets]
    if missing:
        raise SystemExit(
            "Widgets introuvables dans la session : "
            + ", ".join(missing)
            + "\nLe fichier ne correspond pas au format attendu ; patch annule."
        )

    changes = []
    warnings = []
    for wid, rules in REPLACEMENTS.items():
        w = widgets[wid]
        script = w.get("onValue", "")
        for idx, (old, new) in enumerate(rules):
            if new in script:
                warnings.append(f"{wid}[regle {idx}] : deja applique, ignore")
                continue
            if old not in script:
                raise SystemExit(
                    f"{wid}[regle {idx}] : motif attendu introuvable.\n"
                    f"  attendu : {old!r}\n"
                    "Le script a change ; patch annule pour ne rien corrompre."
                )
            script = script.replace(old, new)
            changes.append((wid, idx))
        w["onValue"] = script
    return changes, warnings


def write_target(path, data):
    """Ecrit le JSON, avec bascule sudo si l'ecriture directe echoue."""
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        return "direct"
    except PermissionError:
        pass
    # Fallback : fichier temporaire + cp via sudo.
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        subprocess.run(["sudo", "cp", tmp, path], check=True)
    finally:
        os.unlink(tmp)
    return "sudo"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", default=DEFAULT_TARGET,
                    help="chemin du SPATRevolutionRemote.json a patcher")
    ap.add_argument("--dry-run", action="store_true",
                    help="detecte et affiche les changements sans rien ecrire")
    args = ap.parse_args()

    path = args.target
    if not os.path.isfile(path):
        raise SystemExit(f"Fichier introuvable : {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    print(f"Session : {data.get('createdWith', '?')}")
    print(f"Cible   : {path}\n")

    changes, warnings = apply_patch(data)

    for wid in REPLACEMENTS:
        applied = sum(1 for c, _ in changes if c == wid)
        total = len(REPLACEMENTS[wid])
        state = "deja OK" if applied == 0 else f"{applied}/{total} corrige(s)"
        print(f"  - {wid:<18} {state}")
    for w in warnings:
        print(f"    (info) {w}")

    if not changes:
        print("\nRien a faire : la session est deja patchee.")
        return

    if args.dry_run:
        print(f"\n[dry-run] {len(changes)} substitution(s) prete(s), aucune ecriture.")
        return

    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup = f"{path}.bak-{ts}"
    try:
        shutil.copy2(path, backup)
    except PermissionError:
        subprocess.run(["sudo", "cp", "-p", path, backup], check=True)
    print(f"\nBackup  : {backup}")

    mode = write_target(path, data)
    print(f"Ecriture: {mode}")
    print(f"Patch applique : {len(changes)} substitution(s). Relance la Remote.")


if __name__ == "__main__":
    main()
