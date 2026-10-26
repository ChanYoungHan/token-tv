"""Public plan metadata only; never persist the OAuth profile or credentials."""

LABELS = {'pro': 'Pro', 'max': 'Max', 'max_5x': 'Max 5x', 'max_20x': 'Max 20x',
          'team': 'Team', 'enterprise': 'Enterprise', 'free': 'Free', 'go': 'Go',
          'plus': 'Plus', 'prolite': 'Pro Lite', 'promax': 'Pro Max', 'business': 'Business',
          'edu': 'Edu', 'edu_plus': 'Edu Plus', 'edu_pro': 'Edu Pro',
          'self_serve_business_prolite': 'Business (Pro Lite)',
          'self_serve_business_usage_based': 'Business (사용량 기반)',
          'enterprise_cbp_automation': 'Enterprise (자동화)',
          'enterprise_cbp_usage_based': 'Enterprise (사용량 기반)'}

CODEX_PLANS = frozenset(LABELS) - {'max', 'max_5x', 'max_20x'}


def codex_subscription(account, limits=None):
    """Prefer the verified account; a same-account quota response is a fallback."""
    for source in (account, limits):
        value = source.get('planType') if isinstance(source, dict) else None
        if isinstance(value, str) and value in CODEX_PLANS:
            return metadata(value)
    return metadata(None)


def plan_id(value):
    return value if isinstance(value, str) and value in LABELS else None


def metadata(value):
    value = plan_id(value)
    return {'id': value, 'label': LABELS.get(value, '요금제 미확인')}


def claude_subscription(profile):
    organization = profile.get('organization')
    if not isinstance(organization, dict):
        return metadata(None)
    tier = organization.get('rate_limit_tier')
    tiers = {'default_claude_max_5x': 'max_5x', 'default_claude_max_20x': 'max_20x',
             'default_claude_pro': 'pro', 'default_claude_team': 'team',
             'default_claude_enterprise': 'enterprise'}
    if isinstance(tier, str) and tier in tiers:
        return metadata(tiers[tier])
    kind = organization.get('organization_type')
    return metadata({'claude_pro': 'pro', 'claude_max': 'max', 'claude_team': 'team',
                     'claude_enterprise': 'enterprise'}.get(kind) if isinstance(kind, str) else None)
