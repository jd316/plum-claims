from app.services.confidence import compute

def test_clean_pipeline_above_085():
    c = compute(extraction_quality=.92, rule_certainty=1.0, completeness=1.0,
                verifier_agreement=.9, failures=0)
    assert c.final > 0.85

def test_degradation_visibly_lowers():
    clean = compute(.92, 1.0, 1.0, .9, failures=0).final
    degraded = compute(.92, 1.0, .9, .5, failures=1).final
    assert degraded < clean - 0.1

def test_components_recorded():
    c = compute(.9, .8, .7, .6, failures=2)
    cc = c.components
    assert cc.extraction_quality == .9 and cc.degradation_penalty > 0
