"""Grammar-decoded drive families must equal the seeded catalog families.

Rung 2 materializes a provisional family keyed (vendor, canonicalize_title(
decoded family)); the refdata import keys seeded families the same way
(refdata/persist.py). If the two names differ, one product line gets two
family identities: MS-1e rows decoded to 'ultrastar' while the seeded models
sat under 'ultrastar dc hc550' (s7 D1). This pins the contract from both ends
for every seeded drive model the grammar assigns a family to."""

from hw_radar.matching.grammars import decode
from hw_radar.matching.ladder import brands_consistent
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.refdata.loader import load_seed_documents


def _drive_seed_models() -> list[tuple[str, str, str]]:
    return [
        (doc.manufacturer_key, doc.family_name, model.model_number)
        for doc in load_seed_documents()
        if doc.category == "drive"
        for model in doc.models
    ]


def test_every_decoded_seed_family_equals_the_seed_family() -> None:
    mismatches: list[tuple[str, str, str]] = []
    decoded_families = 0
    for manufacturer, family_name, model_number in _drive_seed_models():
        result = decode(normalize_alias_text(model_number))
        if result is None or result.family_name is None:
            continue  # the grammar asserts no family: nothing to disagree with
        decoded_families += 1
        assert brands_consistent(result.vendor, manufacturer), model_number
        if canonicalize_title(result.family_name) != canonicalize_title(family_name):
            mismatches.append((model_number, result.family_name, family_name))
    assert mismatches == []
    # Guard against a vacuous pass (e.g. a grammar that stops decoding
    # families at all): the WD Red Plus/Red Pro/Gold/Ultrastar and Seagate
    # IronWolf Pro seeds all carry grammar-decodable families.
    assert decoded_families >= 50


def test_ultrastar_seed_family_is_the_line_not_the_series() -> None:
    # The HC5x0 series name survives only in spec.model_family; the family
    # node is the product line the grammar and the owner labels name.
    ultrastar = {
        family_name
        for manufacturer, family_name, model_number in _drive_seed_models()
        if manufacturer == "western_digital" and model_number.startswith("WUH")
    }
    assert ultrastar == {"Ultrastar"}
