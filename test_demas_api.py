import requests, time, json

BASE = "https://apidadosabertos.saude.gov.br"

ENDPOINTS = [
    "/cnes/profissionais-cbo",
    "/cnes/tipo-de-estabelecimento",
    "/cnes/natureza-juridica",
    "/assistencia-a-saude/hospitais-e-leitos",
    "/assistencia-a-saude/unidade-basicas-de-saude",
    "/atencao-primaria/pmmb-profissionais-ativos",
    "/atencao-primaria/equipes-saude-familia",
    "/atencao-primaria/agentes-comunitarios-saude",
]

HEADERS = {"accept": "application/json"}


def find_list(obj):
    """Return (key, list) for the first list value in the JSON dict, else (None, None)."""
    if isinstance(obj, list):
        return ("<root>", obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                return (k, v)
    return (None, None)


def probe(ep):
    print("=" * 80)
    print("ENDPOINT:", ep)
    results = {}
    for offset in (0, 200):
        url = BASE + ep
        params = {"limit": 200, "offset": offset}
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        except Exception as e:
            print(f"  offset={offset}: REQUEST FAILED: {e}")
            time.sleep(0.3)
            continue
        print(f"  --- offset={offset} | HTTP {r.status_code} | final url: {r.url}")
        if r.status_code != 200:
            print("      body (first 300):", r.text[:300])
            time.sleep(0.3)
            continue
        try:
            data = r.json()
        except Exception as e:
            print("      JSON parse failed:", e, "| body:", r.text[:300])
            time.sleep(0.3)
            continue
        if isinstance(data, dict):
            print("      top-level keys:", list(data.keys()))
        else:
            print("      top-level type:", type(data).__name__)
        key, lst = find_list(data)
        if lst is None:
            print("      no list found in response. sample:", json.dumps(data)[:300])
        else:
            print(f"      list key='{key}' len={len(lst)}")
            if lst:
                first = lst[0]
                if isinstance(first, dict):
                    print("      record keys:", list(first.keys()))
                    print("      sample record:", json.dumps(first, ensure_ascii=False)[:400])
                else:
                    print("      first element:", str(first)[:200])
            results[offset] = lst
        time.sleep(0.3)

    # offset comparison
    if 0 in results and 200 in results and results[0] and results[200]:
        same = json.dumps(results[0][0], ensure_ascii=False, sort_keys=True) == \
               json.dumps(results[200][0], ensure_ascii=False, sort_keys=True)
        print(f"  >>> offset effect: first record at offset=0 vs offset=200 "
              f"{'IDENTICAL (offset NOT working)' if same else 'DIFFERENT (offset works)'}")
    elif 0 in results and 200 not in results:
        print("  >>> offset=200 returned no list / empty.")
    return results


if __name__ == "__main__":
    for ep in ENDPOINTS:
        probe(ep)
    print("=" * 80)
    print("DONE")
