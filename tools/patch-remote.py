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
  4. retablit la synchro inter-navigateurs : les ponts Dual/multixy vers le pad
     principal utilisaient `set("variable_xyzpad", ..., {send:false})`, or dans un
     `set()` de script le flag `send` gouverne aussi le message de client-sync
     (`syncOsc`). Consequence : bouger une source depuis les onglets Dual/multi ne
     mettait PAS a jour l'onglet main des autres clients connectes. Le pad principal
     `xy_main` et les faders `spat_position*` font le meme `set` SANS `{send:false}`
     et se synchronisent ; on aligne les 5 ponts sur ce comportement de reference.

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

# Ponts vers le pad principal : le `set("variable_xyzpad", ...)` qui doit se
# synchroniser vers les autres clients. Le `, {send:false}` bloque la client-sync
# et doit sauter. On matche la chaine complete (post-fix-crash, avec le GUARD)
# pour verification/idempotence stricte.
def _sync_fix(inner):
    old = 'set("variable_xyzpad", [' + inner + '], {send:false})'
    new = 'set("variable_xyzpad", [' + inner + '])'
    return (old, new)


XY_INNER = "value[0], value[1], " + GUARD + "[2]"
Z_INNER = GUARD + "[0], " + GUARD + "[1], value"
MULTI_INNER = "value[i * 2], value[i * 2 + 1], " + GUARD + "[2]"

# Substitutions par widget (id -> liste de couples (old, new)).
# Chaque `old` doit etre present tel quel pour que le patch s'applique ;
# chaque `new` sert aussi de marqueur d'idempotence. Les regles crash sont
# listees avant les regles sync (la regle sync matche la chaine deja gardee).
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
        _sync_fix(XY_INNER),
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
        _sync_fix(XY_INNER),
    ],
    "fader_srcDual1_z": [
        (
            '[get("variable_xyzpad")[0], get("variable_xyzpad")[0], value]',
            "[" + GUARD + "[0], " + GUARD + "[1], value]",
        ),
        _sync_fix(Z_INNER),
    ],
    "fader_srcDual2_z": [
        (
            '[get("variable_xyzpad")[0], get("variable_xyzpad")[0], value]',
            "[" + GUARD + "[0], " + GUARD + "[1], value]",
        ),
        _sync_fix(Z_INNER),
    ],
    "multixy_1": [
        (
            'get("variable_xyzpad")[2]',
            GUARD + "[2]",
        ),
        _sync_fix(MULTI_INNER),
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
