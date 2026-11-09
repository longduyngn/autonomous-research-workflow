"""The researcher may provide a machine-readable plan; plain briefs stay direct."""
import json
import re


def parse_plan(output):
    match = re.search(r"```research_plan\s*\n(.*?)```", output, re.DOTALL)
    if not match:
        return {"mode": "direct", "checks": [], "steps": []}
    plan = json.loads(match.group(1))
    if not isinstance(plan, dict) or plan.get("mode") not in ("direct", "steps"):
        raise ValueError("research_plan mode must be direct or steps")
    backend = plan.get('backend', 'local')
    from core.compute import PLATFORMS
    if backend not in {'local', 'colab', 'kaggle',*PLATFORMS}:
        raise ValueError('Unknown research_plan backend')
    steps, checks = plan.get("steps", []), plan.get("checks", [])
    if not isinstance(steps, list) or not isinstance(checks, list):
        raise ValueError("Plan steps/checks must be lists")
    if plan["mode"] == "steps" and not steps:
        raise ValueError("A decomposed plan requires at least one step")
    if len(steps) > 30:
        raise ValueError("Plan has more than 30 steps; narrow the request")
    for item in steps:
        if not isinstance(item, dict) or not isinstance(item.get("instructions"), str):
            raise ValueError("Each step needs instructions")
        if not isinstance(item.get("checks", []), list):
            raise ValueError("Step checks must be a list")
    for check in checks + [c for s in steps for c in s.get("checks", [])]:
        if not isinstance(check, dict) or not isinstance(check.get("argv"), list) or not check["argv"]:
            raise ValueError("Each check needs a nonempty argv array")
        if not all(isinstance(a, str) for a in check["argv"]):
            raise ValueError("Check argv must contain strings")
    parsed = {"mode": plan["mode"], "steps": steps, "checks": checks, 'backend': backend}
    if plan.get('simple') is True:
        parsed['simple'] = True  # Optional hint for the quota router's light validator.
    if 'requires' in plan:
        required = plan['requires']
        if not isinstance(required, dict) or any(
                role not in {'researcher', 'operator', 'validator', 'communicator', 'remote_manager'}
                or not isinstance(accounts, list) or not accounts
                or not all(isinstance(a, str) and a.strip() for a in accounts)
                for role, accounts in required.items()):
            raise ValueError('Plan requires must map roles to nonempty quota-account lists')
        parsed['requires'] = required
    return parsed
