"""Extract the 20 Pillar Summarizer prompt templates verbatim from production source.

Rather than hand-transcribing (which is how prompt drift gets introduced), this
calls the real create_*_prompt() functions with sentinel values chosen so every
interpolated slot renders as a unique, unmistakable token, then reverse-
substitutes those tokens back into {placeholder} form. Every substitution is
asserted to hit exactly once, so a collision fails loudly instead of silently
corrupting a template.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from _source import assert_v1_branch_agnostic, materialize

assert_v1_branch_agnostic()
materialize()

# Written here for inspection/diffing. Installing them is a deliberate second
# step -- see this folder's README.
OUT = os.path.join(_HERE, "out")

# Sentinels. All 6+ digits and mutually non-substring, so reverse substitution
# can never clip part of another number.
TARGET = 222222          # -> max_words; min_words = TARGET-100 = 222122; TARGET-50 = 222172
MIN_W = TARGET - 100
T_M50 = TARGET - 50
PREV = "<<<PREVIOUS_SUMMARY>>>"

# retry: expansion branch needs current < min_words, condensation needs current >= min_words
RETRY_EXPAND_CUR = 100000          # word_diff = 222222-100000 = 122222
RETRY_CONDENSE_CUR = 400000        # word_diff = 222222-400000 = -177778 -> abs 177778

# final retry: reduce needs current > max_words, expand needs current <= max_words.
# Values chosen to land in the highest tier so `over`/`under` render as unique numbers.
FR_OVER = 888888
FR_REDUCE_CUR = TARGET + FR_OVER
FR_UNDER = 999999
FR_EXPAND_CUR = MIN_W - FR_UNDER

DATA_HEADER = {"ar": "عدد الصفوف/الإنجازات: 0\n\n", "en": "Number of rows/achievements: 0\n\n"}

# Anchors surrounding the {operation} slot in the final-retry prompts.
OP_ANCHORS = {
    "ar": ("العملية المطلوبة:\n", "\n\nقواعد صارمة:"),
    "en": ("Required operation:\n", "\n\nStrict rules:"),
}

# Tier boundary probes. The prompt body is identical across tiers -- only the
# injected operation string changes -- so these exist purely to harvest the
# six operation strings per language.
REDUCE_TIER_PROBES = [("le40", 40), ("le80", 80), ("gt80", 200)]
EXPAND_TIER_PROBES = [("le30", 30), ("le70", 70), ("gt70", 200)]


def sub_once(text, needle, replacement, label):
    n = text.count(needle)
    assert n == 1, f"{label}: expected exactly 1 occurrence of {needle!r}, found {n}"
    return text.replace(needle, replacement)


def templatize_common(text, label):
    """Reverse-substitute the word-count sentinels shared by every template."""
    # Longest/most-derived first is unnecessary here (all distinct 6-digit runs),
    # but order is still deterministic for reproducibility.
    for needle, repl in ((str(MIN_W), "{min_words}"), (str(T_M50), "{target_minus_50}"), (str(TARGET), "{max_words}")):
        if needle in text:
            text = text.replace(needle, repl)
    return text


def extract(version, mod):
    res = {}
    for lang in ("ar", "en"):
        # ---- overall ----
        p = mod.create_overall_summary_prompt([], TARGET, lang)
        p = sub_once(p, DATA_HEADER[lang], "{data_text}", f"{version}/overall_{lang}/data_text")
        p = templatize_common(p, f"{version}/overall_{lang}")
        res[f"overall_{lang}"] = p

        # ---- retry: expansion ----
        p = mod.create_retry_prompt([], TARGET, RETRY_EXPAND_CUR, PREV, lang)
        p = sub_once(p, PREV, "{previous_summary}", f"{version}/retry_expand_{lang}/prev")
        p = sub_once(p, str(RETRY_EXPAND_CUR), "{current_word_count}", f"{version}/retry_expand_{lang}/cur")
        p = sub_once(p, str(abs(TARGET - RETRY_EXPAND_CUR)), "{word_diff_abs}", f"{version}/retry_expand_{lang}/diff")
        res[f"retry_expand_{lang}"] = templatize_common(p, f"{version}/retry_expand_{lang}")

        # ---- retry: condensation ----
        p = mod.create_retry_prompt([], TARGET, RETRY_CONDENSE_CUR, PREV, lang)
        p = sub_once(p, PREV, "{previous_summary}", f"{version}/retry_condense_{lang}/prev")
        p = sub_once(p, str(RETRY_CONDENSE_CUR), "{current_word_count}", f"{version}/retry_condense_{lang}/cur")
        p = sub_once(p, str(abs(TARGET - RETRY_CONDENSE_CUR)), "{word_diff_abs}", f"{version}/retry_condense_{lang}/diff")
        res[f"retry_condense_{lang}"] = templatize_common(p, f"{version}/retry_condense_{lang}")

        pre, post = OP_ANCHORS[lang]

        # ---- final retry: reduce ----
        p = mod.create_final_retry_prompt(TARGET, FR_REDUCE_CUR, PREV, lang)
        op = p.split(pre, 1)[1].split(post, 1)[0]
        p = sub_once(p, pre + op + post, pre + "{operation}" + post, f"{version}/final_reduce_{lang}/op")
        p = sub_once(p, PREV, "{previous_summary}", f"{version}/final_reduce_{lang}/prev")
        p = sub_once(p, str(FR_OVER), "{over}", f"{version}/final_reduce_{lang}/over")
        res[f"final_retry_reduce_{lang}"] = templatize_common(p, f"{version}/final_reduce_{lang}")

        # ---- final retry: expand ----
        p = mod.create_final_retry_prompt(TARGET, FR_EXPAND_CUR, PREV, lang)
        op = p.split(pre, 1)[1].split(post, 1)[0]
        p = sub_once(p, pre + op + post, pre + "{operation}" + post, f"{version}/final_expand_{lang}/op")
        p = sub_once(p, PREV, "{previous_summary}", f"{version}/final_expand_{lang}/prev")
        # NOTE the asymmetry with the reduce branch above: `under` is computed in
        # production solely to pick the tier -- it is never interpolated into the
        # undershoot prompt body, whereas `over` IS printed ("reduced by [N words]")
        # in the overshoot one. Asserted rather than assumed, so a future source
        # change that starts using it fails here instead of silently dropping it.
        assert str(FR_UNDER) not in p, f"{version}/final_expand_{lang}: `under` is now interpolated -- add a placeholder"
        res[f"final_retry_expand_{lang}"] = templatize_common(p, f"{version}/final_expand_{lang}")

    # ---- tier operation strings ----
    ops = {"reduce": {}, "expand": {}}
    for lang in ("ar", "en"):
        pre, post = OP_ANCHORS[lang]
        ops["reduce"][lang] = {}
        for name, over in REDUCE_TIER_PROBES:
            p = mod.create_final_retry_prompt(TARGET, TARGET + over, PREV, lang)
            ops["reduce"][lang][name] = p.split(pre, 1)[1].split(post, 1)[0]
        ops["expand"][lang] = {}
        for name, under in EXPAND_TIER_PROBES:
            p = mod.create_final_retry_prompt(TARGET, MIN_W - under, PREV, lang)
            ops["expand"][lang][name] = p.split(pre, 1)[1].split(post, 1)[0]
    return res, ops


PLACEHOLDERS = (
    "{data_text}", "{min_words}", "{max_words}", "{target_minus_50}",
    "{previous_summary}", "{current_word_count}", "{word_diff_abs}",
    "{operation}", "{over}",
)


def escape_literal_braces(text):
    """Escape braces that are literal prompt content, leaving placeholders intact.

    v2's templates embed a literal JSON example ({"summary": ..., "language": ...}).
    In production's source those appear as {{ }} inside the f-string, so storing
    them escaped here is a faithful copy of the source, and lets the whole file
    be .format()ed in one pass -- matching the record_evaluator/attempt_comparator
    convention in prompt_loader.py (production builds each of these prompts as a
    single f-string top to bottom, so fragment-concatenation is not needed).
    """
    for i, ph in enumerate(PLACEHOLDERS):
        text = text.replace(ph, f"\x00{i}\x00")
    text = text.replace("{", "{{").replace("}", "}}")
    for i, ph in enumerate(PLACEHOLDERS):
        text = text.replace(f"\x00{i}\x00", ph)
    return text


# Realistic values for the round-trip check -- deliberately unlike the sentinels.
VERIFY = dict(target=575, rows=[{"الإنجاز": "قيمة", "note": "value"}, {"x": "y"}], prev="ملخص سابق. Previous summary text.")


def verify_roundtrip(version, mod, templates, ops):
    """Re-render every stored template and assert it equals production's output."""
    t = VERIFY["target"]
    minw, maxw = t - 100, t
    checks = 0

    for lang in ("ar", "en"):
        # overall -- data_text is built by the loader, so rebuild it the same way
        header = "عدد الصفوف/الإنجازات: " if lang == "ar" else "Number of rows/achievements: "
        row_lbl = "--- الإنجاز/الصف " if lang == "ar" else "--- Achievement/Row "
        data_text = f"{header}{len(VERIFY['rows'])}\n\n"
        for i, row in enumerate(VERIFY["rows"], 1):
            data_text += f"{row_lbl}{i} ---\n"
            for k, v in row.items():
                if v and str(v).strip() and str(v).strip().lower() != "nan":
                    data_text += f"{k}: {v}\n"
            data_text += "\n"
        got = templates[f"overall_{lang}"].format(
            data_text=data_text, min_words=minw, max_words=maxw, target_minus_50=maxw - 50
        )
        want = mod.create_overall_summary_prompt(VERIFY["rows"], t, lang)
        assert got == want, f"{version}/overall_{lang} round-trip MISMATCH"
        checks += 1

        # retry, both branches
        for branch, cur in (("expand", minw - 60), ("condense", maxw + 90)):
            got = templates[f"retry_{branch}_{lang}"].format(
                min_words=minw, max_words=maxw, target_minus_50=t - 50,
                current_word_count=cur, word_diff_abs=abs(t - cur), previous_summary=VERIFY["prev"],
            )
            want = mod.create_retry_prompt(VERIFY["rows"], t, cur, VERIFY["prev"], lang)
            assert got == want, f"{version}/retry_{branch}_{lang} round-trip MISMATCH"
            checks += 1

        # final retry -- reduce, all three tiers
        for name, over in REDUCE_TIER_PROBES:
            cur = maxw + over
            got = templates[f"final_retry_reduce_{lang}"].format(
                min_words=minw, max_words=maxw, over=over, operation=ops["reduce"][lang][name],
                previous_summary=VERIFY["prev"],
            )
            want = mod.create_final_retry_prompt(t, cur, VERIFY["prev"], lang)
            assert got == want, f"{version}/final_retry_reduce_{lang}[{name}] round-trip MISMATCH"
            checks += 1

        # final retry -- expand, all three tiers
        for name, under in EXPAND_TIER_PROBES:
            cur = minw - under
            got = templates[f"final_retry_expand_{lang}"].format(
                min_words=minw, max_words=maxw, operation=ops["expand"][lang][name],
                previous_summary=VERIFY["prev"],
            )
            want = mod.create_final_retry_prompt(t, cur, VERIFY["prev"], lang)
            assert got == want, f"{version}/final_retry_expand_{lang}[{name}] round-trip MISMATCH"
            checks += 1

    return checks


def main():
    for version, pkg in (("v1", "ps_v1"), ("v2", "ps_v2")):
        mod = __import__(f"{pkg}.utils", fromlist=["utils"])
        templates, ops = extract(version, mod)
        templates = {k: escape_literal_braces(v) for k, v in templates.items()}

        checks = verify_roundtrip(version, mod, templates, ops)

        d = os.path.join(OUT, version)
        os.makedirs(d, exist_ok=True)
        for name, text in templates.items():
            with open(os.path.join(d, f"{name}.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        with open(os.path.join(d, "final_retry_operations.json"), "w", encoding="utf-8") as f:
            json.dump(ops, f, ensure_ascii=False, indent=2)
        print(f"{version}: {len(templates)} templates, {checks} round-trip checks PASSED")
        for name, text in sorted(templates.items()):
            print(f"   {name}.txt  {len(text)} chars")


if __name__ == "__main__":
    main()
