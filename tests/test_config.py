from pathlib import Path

import pytest

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


def test_default_config_loads(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(tmp_path)
    assert config.documentation_root == tmp_path / "plan"
    assert config.areas["roadmap"].as_posix() == "roadmap"
    assert config.catalog_exclusions == ()
    assert config.navigation_extend_through == ()
    assert config.legacy_relation_mode == "strict"
    assert config.snapshot_document_types == ()
    assert config.snapshot_rules == ()
    assert config.graph_health_policy.required_metadata == ()
    assert config.graph_health_policy.report_orphans is False
    assert config.projection_format == "sharded-json"
    assert config.context_views == ()
    assert config.workstream_criteria == ()
    assert config.intake_criteria == ()
    assert config.admission_criteria == ()


def test_workstream_criteria_are_versioned_and_deterministically_ordered(
    tmp_path: Path,
) -> None:
    configured = DEFAULT_CONFIG + """
[[workstreams.criteria]]
id = "verified-delivery"
revision = 2
required_sections = ["mandate", "review-gate"]
required_evidence = ["changes", "checks", "review", "omissions", "risks", "returns"]
max_attempts = 3
safe_fallback = "blocked"

[[workstreams.criteria]]
id = "verified-delivery"
revision = 1
required_sections = ["mandate"]
required_evidence = ["checks", "review", "returns"]
max_attempts = 2
safe_fallback = "blocked"
"""
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")

    criteria = load_config(tmp_path).workstream_criteria
    assert [criterion.reference for criterion in criteria] == [
        "verified-delivery@1",
        "verified-delivery@2",
    ]
    assert criteria[1].required_sections == ("mandate", "review-gate")
    assert criteria[1].max_attempts == 3


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[[workstreams]]\n", "workstreams must be a table"),
        ("[workstreams]\nunknown = true\n", "workstreams has unknown key"),
        ("[workstreams]\ncriteria = {}\n", "must be a list of tables"),
        (
            "[[workstreams.criteria]]\n"
            'id = "Bad_ID"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n',
            "invalid criterion ID",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 0\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n',
            "revision must be a positive integer",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = ["Bad Anchor"]\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n',
            "supported stable anchors",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["logs"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n',
            "may contain only",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 21\n'
            'safe_fallback = "blocked"\n',
            "max_attempts must be between 1 and 20",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "continue"\n',
            "safe_fallback must be 'blocked'",
        ),
        (
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n'
            "[[workstreams.criteria]]\n"
            'id = "delivery"\nrevision = 1\nrequired_sections = []\n'
            'required_evidence = ["checks"]\nmax_attempts = 1\n'
            'safe_fallback = "blocked"\n',
            "criterion is duplicated",
        ),
    ],
)
def test_invalid_workstream_criteria_are_rejected(
    tmp_path: Path, body: str, message: str
) -> None:
    configured = DEFAULT_CONFIG.replace(
        "[workstreams]\n\n",
        body + "\n",
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_intake_criteria_normalize_project_owned_placements(
    tmp_path: Path,
) -> None:
    configured = DEFAULT_CONFIG + """
[[intake.criteria]]
id = "idea-placement"
revision = 1
allowed_decisions = ["update-existing", "create-draft", "create-workstream"]
max_candidates = 8
safe_fallback = "blocked"
draft = { area = "architecture", type = "architecture", identifier = "document", width = 3 }
workstream = { area = "roadmap", type = "workstream", identifier = "roadmap", width = 3 }
"""
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")

    criterion = load_config(tmp_path).intake_criteria[0]
    assert criterion.reference == "idea-placement@1"
    assert criterion.allowed_decisions == (
        "update-existing",
        "create-draft",
        "create-workstream",
    )
    assert criterion.draft.area == "architecture"
    assert criterion.draft.identifier == "document"
    assert criterion.draft.width == 3
    assert criterion.workstream.document_type == "workstream"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[[intake]]\n", "intake must be a table"),
        ("[intake]\nunknown = true\n", "intake has unknown key"),
        ("[intake]\ncriteria = {}\n", "must be a list of tables"),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["invent"]\n'
            'max_candidates = 1\nsafe_fallback = "blocked"\n'
            'draft = { area = "architecture", type = "document", '
            'identifier = "document", width = 3 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "allowed_decisions may contain only",
        ),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["create-draft"]\n'
            'max_candidates = 0\nsafe_fallback = "blocked"\n'
            'draft = { area = "architecture", type = "document", '
            'identifier = "document", width = 3 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "max_candidates must be between 1 and 50",
        ),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["create-draft"]\n'
            'max_candidates = 1\nsafe_fallback = "continue"\n'
            'draft = { area = "architecture", type = "document", '
            'identifier = "document", width = 3 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "safe_fallback must be 'blocked'",
        ),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["create-draft"]\n'
            'max_candidates = 1\nsafe_fallback = "blocked"\n'
            'draft = { area = "missing", type = "document", '
            'identifier = "document", width = 3 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "area must name a configured area",
        ),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["create-draft"]\n'
            'max_candidates = 1\nsafe_fallback = "blocked"\n'
            'draft = { area = "architecture", type = "document", '
            'identifier = "missing", width = 3 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "identifier must name a configured identifier role",
        ),
        (
            "[[intake.criteria]]\n"
            'id = "placement"\nrevision = 1\nallowed_decisions = ["create-draft"]\n'
            'max_candidates = 1\nsafe_fallback = "blocked"\n'
            'draft = { area = "architecture", type = "document", '
            'identifier = "document", width = 0 }\n'
            'workstream = { area = "roadmap", type = "workstream", '
            'identifier = "roadmap", width = 3 }\n',
            "width must be between 1 and 12",
        ),
    ],
)
def test_invalid_intake_criteria_are_rejected(
    tmp_path: Path, body: str, message: str
) -> None:
    configured = DEFAULT_CONFIG.replace("[intake]\n\n", body + "\n")
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_admission_criteria_normalize_bounded_a0_a2_policy(
    tmp_path: Path,
) -> None:
    configured = DEFAULT_CONFIG + """
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local", "run-checks"]
required_authorizations = ["edit-local"]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 12
required_sections = ["mandate", "boundaries", "review-gate"]
safe_fallback = "blocked"
"""
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")

    criterion = load_config(tmp_path).admission_criteria[0]
    assert criterion.reference == "bounded-local@1"
    assert criterion.max_autonomy == "A2"
    assert criterion.allowed_actions == (
        "inspect",
        "plan",
        "edit-local",
        "run-checks",
    )
    assert criterion.required_authorizations == ("edit-local",)
    assert criterion.allowed_verification == ("focused", "full")
    assert criterion.required_sections == (
        "mandate",
        "boundaries",
        "review-gate",
    )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('max_autonomy = "A4"', "max_autonomy must be A0, A1 or A2"),
        (
            'max_autonomy = "A1"',
            "allowed_actions exceeds max_autonomy A1",
        ),
        (
            'required_authorizations = ["run-checks"]',
            "must be allowed actions",
        ),
        (
            'allowed_actions = ["execute"]',
            "allowed_actions may contain only",
        ),
        (
            'allowed_verification = ["deep"]',
            "allowed_verification may contain only",
        ),
        ('max_risk = "critical"', "max_risk must be low, medium or high"),
        ("max_targets = 0", "max_targets must be between 1 and 100"),
        ("required_sections = []", "required_sections must not be empty"),
        (
            'required_sections = ["Bad Anchor"]',
            "must contain supported stable anchors",
        ),
        ('safe_fallback = "continue"', "safe_fallback must be 'blocked'"),
    ],
)
def test_invalid_admission_criteria_are_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    body = """
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local"]
required_authorizations = ["edit-local"]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 12
required_sections = ["mandate", "boundaries", "review-gate"]
safe_fallback = "blocked"
"""
    field = replacement.split(" =", 1)[0]
    lines = [
        replacement if line.startswith(f"{field} =") else line
        for line in body.splitlines()
    ]
    (tmp_path / CONFIG_FILENAME).write_text(
        DEFAULT_CONFIG + "\n".join(lines) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_context_views_are_validated_and_ordered_by_tier(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG + """
[context.views.task]
tier = 2
delivery = "navigation"
direction = "forward"
depth = 1
relations = ["depends_on", "derived_from", "validated_against"]
layers = ["authored"]

[context.views.map]
tier = 1
delivery = "outline"
direction = "both"
depth = 0
relations = []
layers = ["authored"]
"""
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    views = load_config(tmp_path).context_views
    assert [view.name for view in views] == ["map", "task"]
    assert views[0].delivery == "outline"
    assert views[0].direction == "both"
    assert views[1].relations == (
        "depends_on",
        "derived_from",
        "validated_against",
    )


def test_graph_health_policy_is_optional_and_normalized(tmp_path: Path) -> None:
    configured = DEFAULT_CONFIG.replace(
        "required_metadata = []\nreport_orphans = false",
        'hub_in_degree = 3\nhub_out_degree = 4\nboundary_count = 2\n'
        'stale_pin_count = 2\nmax_weak_components = 1\n'
        'required_metadata = ["type", "status"]\nreport_orphans = true',
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    policy = load_config(tmp_path).graph_health_policy
    assert policy.hub_in_degree == 3
    assert policy.hub_out_degree == 4
    assert policy.boundary_count == 2
    assert policy.stale_pin_count == 2
    assert policy.max_weak_components == 1
    assert policy.required_metadata == ("type", "status")
    assert policy.report_orphans is True

    legacy = configured.replace(
        "[graph_health]\n"
        "hub_in_degree = 3\nhub_out_degree = 4\nboundary_count = 2\n"
        "stale_pin_count = 2\nmax_weak_components = 1\n"
        'required_metadata = ["type", "status"]\nreport_orphans = true\n\n',
        "",
    )
    (tmp_path / CONFIG_FILENAME).write_text(legacy, encoding="utf-8")
    assert load_config(tmp_path).graph_health_policy.required_metadata == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[[graph_health]]\n", "graph_health must be a table"),
        ("[graph_health]\nunknown = 1\n", "graph_health has unknown key"),
        ("[graph_health]\nhub_in_degree = 0\n", "must be a positive integer"),
        ("[graph_health]\nhub_out_degree = true\n", "must be a positive integer"),
        (
            '[graph_health]\nrequired_metadata = ["owner"]\n',
            "may contain only 'type' and 'status'",
        ),
        (
            '[graph_health]\nrequired_metadata = ["type", "type"]\n',
            "required_metadata must be unique",
        ),
        (
            '[graph_health]\nreport_orphans = "yes"\n',
            "report_orphans must be a boolean",
        ),
    ],
)
def test_invalid_graph_health_policy_is_rejected(
    tmp_path: Path, body: str, message: str
) -> None:
    configured = DEFAULT_CONFIG.replace(
        "[graph_health]\nrequired_metadata = []\nreport_orphans = false\n\n",
        body,
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("context", "message"),
    [
        ("[[context]]\n", "context must be a table"),
        ("[context]\nunknown = true\n", "context has unknown key"),
        ("[context]\nviews = []\n", "context.views must be a table"),
        (
            "[context.views.Bad_Name]\n",
            "context.views.Bad_Name has an invalid view name",
        ),
        (
            "[context.views.task]\ntier = 1\n",
            "is missing required key",
        ),
        (
            "[context.views.task]\n"
            "tier = 1\ndelivery = \"full\"\ndirection = \"forward\"\n"
            "depth = 1\nrelations = []\nlayers = [\"authored\"]\n",
            "delivery must be 'outline' or 'navigation'",
        ),
        (
            "[context.views.task]\n"
            "tier = 1\ndelivery = \"navigation\"\ndirection = \"sideways\"\n"
            "depth = 1\nrelations = []\nlayers = [\"authored\"]\n",
            "direction must be 'forward', 'reverse' or 'both'",
        ),
        (
            "[context.views.task]\n"
            "tier = 1\ndelivery = \"navigation\"\ndirection = \"forward\"\n"
            "depth = 6\nrelations = []\nlayers = [\"authored\"]\n",
            "depth must be an integer between 0 and 5",
        ),
        (
            "[context.views.task]\n"
            "tier = 1\ndelivery = \"navigation\"\ndirection = \"forward\"\n"
            "depth = 1\nrelations = [\"references\"]\n"
            "layers = [\"authored\"]\n",
            "relations must contain only supported semantic relations",
        ),
        (
            "[context.views.task]\n"
            "tier = 1\ndelivery = \"navigation\"\ndirection = \"forward\"\n"
            "depth = 1\nrelations = []\nlayers = [\"observed\"]\n",
            "layers must currently be exactly",
        ),
    ],
)
def test_invalid_context_views_are_rejected(
    tmp_path: Path, context: str, message: str
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG + context, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_context_view_tiers_and_relations_must_be_unique(tmp_path: Path) -> None:
    view = """
[context.views.one]
tier = 1
delivery = "navigation"
direction = "forward"
depth = 1
relations = ["depends_on", "depends_on"]
layers = ["authored"]
"""
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG + view, encoding="utf-8")
    with pytest.raises(ValueError, match="relations must be unique"):
        load_config(tmp_path)

    duplicate_tier = view.replace(
        'relations = ["depends_on", "depends_on"]',
        'relations = ["depends_on"]',
    ) + """
[context.views.two]
tier = 1
delivery = "outline"
direction = "reverse"
depth = 0
relations = []
layers = ["authored"]
"""
    (tmp_path / CONFIG_FILENAME).write_text(
        DEFAULT_CONFIG + duplicate_tier, encoding="utf-8"
    )
    with pytest.raises(ValueError, match="context view tier is duplicated: 1"):
        load_config(tmp_path)


def test_area_paths_must_be_unique(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace('reviews = "reviews"', 'reviews = "roadmap"')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="area paths must be unique"):
        load_config(tmp_path)


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace('root = "plan"', 'root = "../private"')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="project-relative"):
        load_config(tmp_path)


def test_catalog_table_is_optional_for_existing_configuration(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace("[catalog]\nexclude = []\n\n", "")
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    assert load_config(tmp_path).catalog_exclusions == ()


def test_catalog_must_be_a_table(tmp_path: Path) -> None:
    config = 'catalog = "invalid"\n' + DEFAULT_CONFIG.replace(
        "[catalog]\nexclude = []\n\n", ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match="catalog must be a table"):
        load_config(tmp_path)


def test_catalog_exclusions_are_ordered_and_normalized(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace(
        "exclude = []",
        'exclude = ["./templates//*-template.md", "resources/**/*.md"]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    assert load_config(tmp_path).catalog_exclusions == (
        "templates/*-template.md",
        "resources/**/*.md",
    )


def test_navigation_table_is_optional_and_preserves_anchor_order(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace(
        'extend_through = []', 'extend_through = ["резюме", "contents"]'
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    assert load_config(tmp_path).navigation_extend_through == (
        "резюме",
        "contents",
    )

    legacy = config.replace(
        '[navigation]\nextend_through = ["резюме", "contents"]\n\n', ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(legacy, encoding="utf-8")
    assert load_config(tmp_path).navigation_extend_through == ()


def test_navigation_must_be_a_table(tmp_path: Path) -> None:
    config = 'navigation = "invalid"\n' + DEFAULT_CONFIG.replace(
        "[navigation]\nextend_through = []\n\n", ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match="navigation must be a table"):
        load_config(tmp_path)


def test_relations_table_is_optional_and_loads_adoption_policy(
    tmp_path: Path,
) -> None:
    config = DEFAULT_CONFIG.replace(
        'legacy_paths = "strict"',
        'legacy_paths = "resolve-with-warning"',
    ).replace(
        "snapshot_types = []",
        'snapshot_types = ["review", "experiment"]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert loaded.legacy_relation_mode == "resolve-with-warning"
    assert loaded.snapshot_document_types == ("review", "experiment")
    assert loaded.snapshot_rules == ()

    legacy = config.replace(
        '[relations]\nlegacy_paths = "resolve-with-warning"\n'
        'snapshot_types = ["review", "experiment"]\n'
        'snapshot_rules = []\n\n',
        "",
    )
    (tmp_path / CONFIG_FILENAME).write_text(legacy, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert loaded.legacy_relation_mode == "strict"
    assert loaded.snapshot_document_types == ()
    assert loaded.snapshot_rules == ()


def test_status_aware_snapshot_rules_are_validated(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace(
        "snapshot_rules = []",
        'snapshot_rules = [{ source_type = "roadmap", '
        'source_status = "completed" }, { source_status = "archived" }]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    rules = load_config(tmp_path).snapshot_rules
    assert len(rules) == 2
    assert rules[0].matches("roadmap", "completed")
    assert not rules[0].matches("roadmap", "active")
    assert rules[1].matches("decision", "archived")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('"roadmap"', "must be a list of tables"),
        ('["roadmap"]', "must be a table"),
        ("[{}]", "must define source_type, source_status, or both"),
        (
            '[{ source_type = "roadmap", unknown = "value" }]',
            "has unknown key",
        ),
        ('[{ source_status = "" }]', "source_status must be a non-empty string"),
        (
            '[{ source_status = "completed" }, '
            '{ source_status = " completed " }]',
            "contains duplicate rule",
        ),
    ],
)
def test_invalid_snapshot_rules_are_rejected(
    tmp_path: Path, value: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace("snapshot_rules = []", f"snapshot_rules = {value}")
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("relations", "message"),
    [
        ("[[relations]]\n", "relations must be a table"),
        (
            '[relations]\nlegacy_paths = "accept"\nsnapshot_types = []\n',
            "relations.legacy_paths must be 'strict' or 'resolve-with-warning'",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\nsnapshot_types = "review"\n',
            "relations.snapshot_types must be a list of non-empty strings",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\nsnapshot_types = [""]\n',
            "relations.snapshot_types must be a list of non-empty strings",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\n'
            'snapshot_types = ["review", "review"]\n',
            "relations.snapshot_types must be unique",
        ),
    ],
)
def test_invalid_relations_policy_is_rejected(
    tmp_path: Path, relations: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace(
        '[relations]\nlegacy_paths = "strict"\nsnapshot_types = []\n',
        relations,
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('extend_through = ""', "navigation.extend_through must be a list"),
        (
            'extend_through = [""]',
            r"navigation\.extend_through\[0] must be a non-empty string",
        ),
        (
            "extend_through = [1]",
            r"navigation\.extend_through\[0] must be a non-empty string",
        ),
        (
            'extend_through = ["bad anchor"]',
            r"navigation\.extend_through\[0] has unsupported anchor syntax",
        ),
        (
            'extend_through = ["summary", "summary"]',
            "navigation.extend_through contains duplicate anchor 'summary'",
        ),
    ],
)
def test_invalid_navigation_configuration_is_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace("extend_through = []", replacement)
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('exclude = ""', "catalog.exclude must be a list"),
        ('exclude = [""]', r"catalog\.exclude\[0] must be a non-empty string"),
        ('exclude = [1]', r"catalog\.exclude\[0] must be a non-empty string"),
        (
            'exclude = ["/templates/*.md"]',
            r"catalog\.exclude\[0] must be relative to the documentation root",
        ),
        (
            'exclude = ["../templates/*.md"]',
            r"catalog\.exclude\[0] must be relative to the documentation root",
        ),
        (
            'exclude = ["templates\\\\*.md"]',
            r"catalog\.exclude\[0] must use POSIX '/' separators",
        ),
        (
            'exclude = ["templates/*.md", "templates//*.md"]',
            "duplicate normalized pattern 'templates/\\*\\.md'",
        ),
    ],
)
def test_invalid_catalog_exclusions_are_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace("exclude = []", replacement)
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_maintenance_table_is_optional(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    assert load_config(tmp_path).maintenance_targets == ()


_MINIMAL_MAINTENANCE = """
[[maintenance]]
name = "install-version"
source_document = "DOC-001"
source_anchor = "install-block"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "quickstart"
role = "current"
"""

_EXTENDED_MAINTENANCE = """
[[maintenance]]
name = "install-version"
source_document = "DOC-001"
source_anchor = "install-block"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "quickstart"
role = "current"

[[maintenance.occurrences]]
document = "DOC-003"
anchor = "changelog"
role = "historical"

[[maintenance]]
name = "second-target"
source_document = "DOC-004"
source_anchor = "canonical"

[[maintenance.occurrences]]
document = "DOC-005"
anchor = "replica"
role = "current"
"""


def test_minimal_maintenance_config_loads(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG + _MINIMAL_MAINTENANCE
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert len(loaded.maintenance_targets) == 1
    target = loaded.maintenance_targets[0]
    assert target.name == "install-version"
    assert target.source_document_id == "DOC-001"
    assert target.source_anchor == "install-block"
    assert len(target.occurrences) == 1
    assert target.occurrences[0].document_id == "DOC-002"
    assert target.occurrences[0].anchor == "quickstart"
    assert target.occurrences[0].role == "current"


def test_extended_maintenance_config_with_multiple_targets_loads(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG + _EXTENDED_MAINTENANCE
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert [target.name for target in loaded.maintenance_targets] == [
        "install-version",
        "second-target",
    ]
    assert len(loaded.maintenance_targets[0].occurrences) == 2
    assert loaded.maintenance_targets[0].occurrences[1].role == "historical"


@pytest.mark.parametrize(
    ("maintenance_toml", "message"),
    [
        ("maintenance = \"invalid\"\n", "maintenance must be a list of tables"),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n'
            'extra_key = "x"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\] has unknown key\(s\): extra_key",
        ),
        (
            "[[maintenance]]\n"
            'name = ""\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.name must be a non-empty identifier-style string",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n\n'
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-004"\n'
            'source_anchor = "canonical"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-005"\n'
            'anchor = "replica"\n'
            'role = "current"\n',
            "maintenance target name is duplicated: 't'",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n'
            "occurrences = []\n",
            r"maintenance\[0\]\.occurrences must be a non-empty list",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "not-an-id"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.source_document must use a configured stable ID prefix",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "bad anchor"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.source_anchor must use the supported stable anchor syntax",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "invented-role"\n',
            r"maintenance\[0\]\.occurrences\[0\]\.role must be one of:",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n'
            'extra = "x"\n',
            r"maintenance\[0\]\.occurrences\[0\] has unknown key\(s\): extra",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-001"\n'
            'anchor = "install-block"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.occurrences\[0\] overlaps the declared source address",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "historical"\n',
            r"maintenance\[0\]\.occurrences\[1\] duplicates another occurrence at",
        ),
    ],
)
def test_invalid_maintenance_configuration_is_rejected(
    tmp_path: Path, maintenance_toml: str, message: str
) -> None:
    # A bare `key = value` line must precede every `[table]` header in TOML,
    # or it becomes a key of whatever table was last opened; only the
    # scalar-assignment case needs to be prepended for that reason.
    config = (
        maintenance_toml + DEFAULT_CONFIG
        if maintenance_toml.startswith("maintenance =")
        else DEFAULT_CONFIG + maintenance_toml
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)
