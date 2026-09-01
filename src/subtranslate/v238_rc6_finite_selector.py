"""V2.3.8 RC6 finite candidate semantic selector."""
from __future__ import annotations
import hashlib, itertools, json, random
from typing import Any
import jsonschema
from v238_rc3_atom_owner_vector import target_atoms

MAX_FINITE_SELECTOR_CANDIDATES = 256
NONE_OF_THE_ABOVE = "NONE_OF_THE_ABOVE"
SELECTOR_SCHEMA = {"type":"array","minItems":1,"maxItems":1,"items":{"type":"integer","minimum":1}}

def positive_compositions(total: int, parts: int):
    if parts < 1 or total < parts: return
    for cuts in itertools.combinations(range(1, total), parts - 1):
        points=(0,)+cuts+(total,)
        yield tuple(points[i+1]-points[i] for i in range(parts))

def owner_vector_from_runs(owner_order: tuple[int,...], run_lengths: tuple[int,...]) -> tuple[int,...]:
    return tuple(owner for owner,count in zip(owner_order,run_lengths) for _ in range(count))

def expand_run_allocation(rows: list[list[int]] | list[tuple[int,int]], *, atom_count: int) -> tuple[list[int] | None, dict[str,Any]]:
    vector=[owner for owner,count in rows for _ in range(count)]
    trace={"atom_count":atom_count,"expanded_vector":vector,"expanded_length":len(vector),"valid":len(vector)==atom_count}
    if not trace["valid"]: trace["reason"]="EXPANDED_VECTOR_LENGTH_MISMATCH"
    else: trace["reason"]="DETERMINISTIC_EXPANSION_PASS"
    return (vector if trace["valid"] else None),trace

def candidate_hash(owner_order: tuple[int,...], run_lengths: tuple[int,...]) -> str:
    value={"owner_order":list(owner_order),"run_lengths":list(run_lengths)}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

def generate_candidates(owner_count: int, atom_count: int, *, max_candidates: int=MAX_FINITE_SELECTOR_CANDIDATES) -> list[dict[str,Any]]:
    if atom_count < owner_count: raise ValueError("FINITE_SELECTOR_M_LT_N")
    expected=math_count(owner_count,atom_count)
    if expected > max_candidates: raise ValueError("FINITE_SELECTOR_SPACE_TOO_LARGE")
    result=[]
    for order in itertools.permutations(range(1,owner_count+1)):
        for lengths in positive_compositions(atom_count,owner_count):
            vector=owner_vector_from_runs(order,lengths)
            ranges=[]; cursor=1
            for owner,count in zip(order,lengths):
                ranges.append({"owner":owner,"start":cursor,"end":cursor+count-1,"count":count}); cursor+=count
            result.append({"canonical_candidate_hash":candidate_hash(order,lengths),"canonical_owner_vector":list(vector),"owner_order":list(order),"run_lengths":list(lengths),"owner_ranges":ranges,"atom_count":atom_count,"source_owner_count":owner_count,"provenance":{"generator":"permutations_x_positive_compositions","formula":"N!*C(M-1,N-1)"}})
    if len({x["canonical_candidate_hash"] for x in result}) != len(result): raise AssertionError("CANDIDATE_HASH_DUPLICATE")
    return result

def math_count(owner_count:int, atom_count:int)->int:
    value=1
    for i in range(1,owner_count+1): value*=i
    choose=1
    for i in range(1,owner_count): choose=choose*(atom_count-i)//i
    return value*choose

def make_presentation(candidates:list[dict[str,Any]], *, seed:int, owner_labels:dict[int,str]|None=None)->dict[str,Any]:
    rng=random.Random(seed); order=list(range(len(candidates))); rng.shuffle(order)
    choice_to_candidate={i+1: candidates[index] for i,index in enumerate(order)}
    canonical_to_presented={i:i for i in range(1, len(candidates[0]["owner_order"])+1)}
    if owner_labels:
        canonical_to_presented=dict(owner_labels)
    else:
        labels=list(canonical_to_presented.values()); rng.shuffle(labels); canonical_to_presented={i:label for i,label in enumerate(labels,1)}
    presented_labels={canonical_to_presented[i]: chr(64+canonical_to_presented[i]) for i in canonical_to_presented}
    catalog=[]
    for choice_id,candidate in choice_to_candidate.items():
        runs=[]
        for owner,count in zip(candidate["owner_order"],candidate["run_lengths"]):
            runs.append(f"{presented_labels[canonical_to_presented[owner]]}[{candidate['owner_ranges'][len(runs)]['start']}-{candidate['owner_ranges'][len(runs)]['end']}]")
        catalog.append(f"CHOICE {choice_id}: {' '.join(runs)}")
    none_id=len(candidates)+1
    return {"presentation_seed":seed,"choice_to_canonical_hash":{str(k):v["canonical_candidate_hash"] for k,v in choice_to_candidate.items()},"choice_to_candidate":choice_to_candidate,"catalog_order":[c["canonical_candidate_hash"] for c in choice_to_candidate.values()],"canonical_to_presented":canonical_to_presented,"presented_labels":presented_labels,"none_choice_id":none_id,"catalog":catalog}

def selector_schema()->dict[str,Any]: return json.loads(json.dumps(SELECTOR_SCHEMA))

def build_selector_request(*, semantic_group_id:str, owners:list[dict[str,str]], target:str, presentation:dict[str,Any], model:str)->dict[str,Any]:
    atoms=target_atoms(target); labels=presentation["canonical_to_presented"]
    source="\n".join(f"OWNER {chr(64+labels[i])}: {owner['source_text']}" for i,owner in enumerate(owners,1))
    user="\n".join([f"SEMANTIC_GROUP: {semantic_group_id}","TASK: SELECT ONE FINITE CANDIDATE; DO NOT BUILD A MAPPING.","SOURCE OWNERS:",source,"TARGET ATOMS (immutable, shown once):"]+[f"ATOM {i}: {a}" for i,a in enumerate(atoms,1)]+["CANDIDATE CATALOG:"]+presentation["catalog"]+[f"NONE CHOICE: {presentation['none_choice_id']}","Return only a JSON root array containing exactly one integer choice ID. Choose NONE only if no listed candidate represents the semantic ownership."])
    return {"model":model,"messages":[{"role":"system","content":"Select exactly one finite candidate. Return only [choice_id]. Do not return mappings, text, or objects."},{"role":"user","content":user}],"format":selector_schema(),"options":{"temperature":0.0,"num_ctx":2560,"num_predict":32},"stream":False,"think":False,"keep_alive":"30m"}

def validate_selector_output(value:Any,presentation:dict[str,Any])->tuple[int|None,dict[str,Any]]:
    try: jsonschema.Draft7Validator(selector_schema()).validate(value)
    except jsonschema.ValidationError as exc: return None,{"valid":False,"reason":"STRICT_SELECTOR_SCHEMA","detail":exc.message}
    choice=int(value[0]); known=choice in presentation["choice_to_candidate"] or choice==presentation["none_choice_id"]
    if not known:return None,{"valid":False,"reason":"UNKNOWN_CHOICE_ID","choice_id":choice}
    if choice==presentation["none_choice_id"]:return None,{"valid":False,"reason":"NONE_OF_THE_ABOVE"}
    return choice,{"valid":True,"reason":"FINITE_SELECTOR_CHOICE_VALID","choice_id":choice,"canonical_candidate_hash":presentation["choice_to_canonical_hash"][str(choice)]}
