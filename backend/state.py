from dataclasses import dataclass, field
from .semantics import ambiguous_person_relation, person_interaction


@dataclass
class ObjectState:
    last_direct: dict | None = None
    last_position: dict | None = None
    container_event: dict | None = None
    visibility: str = "not_observed"
    ambiguous: bool = False
    history: list[dict] = field(default_factory=list)


def replay(document: dict, as_of_ms: int) -> dict[str, ObjectState]:
    entities = {e['id']: e for e in document['entities']}
    states = {e["id"]: ObjectState(ambiguous=e["identity_status"] == "ambiguous") for e in document["entities"]}
    # Ambiguities are retained rather than resolving overlapping contradictory events by arbitrary IDs.
    ordered = sorted((e for e in document["events"] if e["end_ms"] <= as_of_ms), key=lambda e: (e["start_ms"], e["end_ms"]))
    for event in ordered:
        state = states[event["subject_id"]]
        previous = next((e for e in reversed(state.history) if e['epistemic_type'] == 'observed'), None)
        if (event['epistemic_type'] == 'observed' and previous and event["start_ms"] < previous["end_ms"]
                and (event["action"], event["target_id"]) != (previous["action"], previous["target_id"])
                and not person_interaction(event, entities) and not person_interaction(previous, entities)):
            state.ambiguous = True
        state.history.append(event)
        if event["epistemic_type"] != "observed":
            state.visibility = "uncertain"
            continue
        if event["action"] in {"unknown", "occluded"}:
            state.visibility = "occluded" if event["action"] == "occluded" else "uncertain"
            continue
        state.last_direct = event
        if person_interaction(event, entities) or ambiguous_person_relation(event, entities):
            state.visibility = 'uncertain' if ambiguous_person_relation(event, entities) else 'observed_at_time'
            continue  # The held object is not the person's location or container.
        if event["action"] in {"seen_at", "place_on", "pick_up", "put_in", "take_out", "move", "explicit_exit"}:
            state.last_position = event
        state.visibility = "out_of_view" if event["action"] == "explicit_exit" else "observed_at_time"
        if event["action"] == "put_in":
            state.container_event = event
            state.visibility = "inside_not_visible"
        elif event["action"] in {"take_out", "pick_up", "place_on", "seen_at", "move"}:
            # Any new direct observation supersedes the old hidden containment assumption.
            state.container_event = None
    return states
