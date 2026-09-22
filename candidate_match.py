"""Candidate matching based on the supplied CV.

Only job-relevant qualifications are stored here; no personal contact data.
"""

STRONG_ROLE_TERMS = {
    "Customer Success": [
        "customer success", "client success", "customer service", "customer support",
        "customer experience", "client experience", "customer retention", "onboarding",
    ],
    "Account Management": [
        "account manager", "account management", "key account", "account executive",
        "client manager", "relationship manager", "customer account",
    ],
    "Sales": [
        "sales", "business development", "business developer", "commercial",
        "inside sales", "sales executive", "sales representative", "lead generation",
        "prospecting", "revenue",
    ],
    "Administration & Operations": [
        "administrative", "administration", "office manager", "back office",
        "operations", "operations coordinator", "business operations",
        "commercial assistant", "administrative assistant", "logistics coordinator",
        "procurement", "sourcing", "supply chain",
    ],
    "Marketing & E-commerce": [
        "e-commerce", "ecommerce", "shopify", "digital marketing", "marketing",
        "digital ads", "social media", "content",
    ],
    "Design / UX": [
        "ux", "ui", "ux/ui", "product design", "graphic design", "digital design",
        "visual design", "figma",
    ],
}

SKILL_TERMS = [
    "b2b", "b2c", "crm", "salesforce", "zendesk", "shopify", "sage", "excel",
    "tableau", "notion", "google workspace", "figma", "canva", "meta ads",
    "customer onboarding", "retention", "renewals", "supplier", "procurement",
    "logistics", "account management", "client relations", "negotiation",
    "lead generation", "upselling", "cross-functional", "reporting",
    "french", "english", "arabic", "german",
]

EXCLUDE_TERMS = [
    "software engineer", "software developer", "backend developer",
    "frontend developer", "full stack", "data scientist", "machine learning",
    "devops", "cybersecurity", "physician", "nurse", "lawyer", "accountant",
    "architect", "director", "vice president", "chief ", "head of ",
    "senior director",
]

def candidate_fit(title, description="", location="", remote=False):
    import re
    text = re.sub(r"\\s+", " ", f"{title} {description}").lower()
    title_l = str(title or "").lower()
    reasons = []
    score = 35

    if any(x in text for x in EXCLUDE_TERMS):
        return 0, ["Excluded role/seniority mismatch"]

    family_scores = []
    for family, terms in STRONG_ROLE_TERMS.items():
        title_hits = [t for t in terms if t in title_l]
        text_hits = [t for t in terms if t in text]
        if title_hits:
            family_scores.append((family, 42, title_hits[:3]))
        elif text_hits:
            family_scores.append((family, 25, text_hits[:3]))

    if family_scores:
        family, points, hits = max(family_scores, key=lambda x: x[1])
        score += points
        reasons.append(f"{family}: {', '.join(hits)}")

    hits = [s for s in SKILL_TERMS if s in text]
    unique_hits = list(dict.fromkeys(hits))
    score += min(18, len(unique_hits) * 3)
    if unique_hits:
        reasons.append("Skills: " + ", ".join(unique_hits[:6]))

    if any(x in text for x in ["french", "français", "english", "anglais", "arabic", "arabe"]):
        score += 5
        reasons.append("International/multilingual environment")
    if "german" in text or "deutsch" in text:
        score += 3
        reasons.append("German listed as a working/progressing language")

    loc = str(location or "").lower()
    if "casablanca" in loc or "morocco" in loc or "remote" in loc or remote:
        score += 3
        reasons.append("Location/remote compatible")

    score = min(100, score)
    reasons.append("Strong match" if score >= 70 else "Possible match" if score >= 55 else "Low CV match")
    return score, reasons
