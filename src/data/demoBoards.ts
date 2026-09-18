// A demo card. `blockedByIndices` / `dependsOnIndices` reference OTHER cards in the SAME board by
// their position here; loadDemoData resolves them to real ids at load (ids don't exist until then).
// Optional so most cards can omit them — the loader defaults to no dependency.
export interface DemoCard {
  title: string;
  description: string;
  priority: string;
  dueInDays?: number | null;
  assigneeIndices: number[];
  columnIndex: number;
  completed: boolean;
  jiraKey: string;
  storyPoints: number;
  labels: string[];
  blockedByIndices?: number[];
  dependsOnIndices?: number[];
}

export interface DemoBoard {
  title: string;
  description: string;
  columns: { title: string }[];
  teamMembers: { name: string; initials: string; color: string }[];
  cards: DemoCard[];
}

export const DEMO_BOARDS: DemoBoard[] = [
  {
    title: 'Engineering',
    description: 'Core platform engineering tasks and sprint work',
    columns: [{ title: 'Backlog' }, { title: 'In Progress' }, { title: 'In Review' }, { title: 'Done' }],
    teamMembers: [
      { name: 'Alex Kim', initials: 'AK', color: 'bg-blue-500' },
      { name: 'Priya Sharma', initials: 'PS', color: 'bg-purple-500' },
      { name: 'Marcus Chen', initials: 'MC', color: 'bg-green-500' },
      { name: 'Jordan Lee', initials: 'JL', color: 'bg-orange-500' }
    ],
    cards: [
      { title: 'Refactor auth middleware', description: 'Extract JWT validation into a reusable service', priority: 'high', dueInDays: 9, assigneeIndices: [0], columnIndex: 1, completed: false, jiraKey: 'ENG-1041', storyPoints: 8, labels: ['backend', 'security'] },
      { title: 'Add Redis caching layer', description: 'Cache API responses to reduce database load', priority: 'medium', dueInDays: 19, assigneeIndices: [1], columnIndex: 0, completed: false, jiraKey: 'ENG-1055', storyPoints: 5, labels: ['backend', 'performance'] },
      { title: 'Fix race condition in job queue', description: 'Concurrent dispatch causing duplicate processing', priority: 'high', dueInDays: -2, assigneeIndices: [2], columnIndex: 2, completed: false, jiraKey: 'ENG-1038', storyPoints: 13, labels: ['bug', 'backend'] },
      { title: 'Upgrade Node.js to v22', description: 'Update runtime for LTS and performance gains', priority: 'low', dueInDays: 28, assigneeIndices: [3], columnIndex: 0, completed: false, jiraKey: 'ENG-1060', storyPoints: 2, labels: ['devops'] },
      { title: 'Unit tests for billing service', description: 'Target 80% coverage on payment flows', priority: 'medium', dueInDays: 1, assigneeIndices: [0, 1], columnIndex: 1, completed: false, jiraKey: 'ENG-1049', storyPoints: 5, labels: ['testing', 'billing'], blockedByIndices: [0] },
      { title: 'Database index optimisation', description: 'Missing indexes found on orders table via query plan analysis', priority: 'medium', dueInDays: 2, assigneeIndices: [2], columnIndex: 3, completed: true, jiraKey: 'ENG-1022', storyPoints: 3, labels: ['database', 'performance'] }
    ]
  },
  {
    title: 'Engineering × PM',
    description: 'Feature alignment between engineering and product management',
    columns: [{ title: 'Requested' }, { title: 'Scoped' }, { title: 'In Dev' }, { title: 'QA' }, { title: 'Shipped' }],
    teamMembers: [
      { name: 'Taylor Reyes', initials: 'TR', color: 'bg-indigo-500' },
      { name: 'Morgan Blake', initials: 'MB', color: 'bg-pink-500' },
      { name: 'Alex Kim', initials: 'AK', color: 'bg-blue-500' }
    ],
    cards: [
      { title: 'Dark mode support', description: 'System-level and manual theme switching', priority: 'medium', dueInDays: 17, assigneeIndices: [2], columnIndex: 2, completed: false, jiraKey: 'ENG-1067', storyPoints: 8, labels: ['frontend', 'ui'] },
      { title: 'CSV export for reports', description: 'Allow users to download filtered report data', priority: 'high', dueInDays: 10, assigneeIndices: [0], columnIndex: 3, completed: false, jiraKey: 'PM-208', storyPoints: 5, labels: ['data', 'reporting'] },
      { title: 'Two-factor authentication', description: 'TOTP and SMS-based 2FA options', priority: 'high', dueInDays: 23, assigneeIndices: [1, 2], columnIndex: 1, completed: false, jiraKey: 'ENG-1071', storyPoints: 13, labels: ['security', 'auth'] },
      { title: 'Mobile responsive dashboard', description: 'Optimise all views for 375px screens', priority: 'medium', dueInDays: 33, assigneeIndices: [0], columnIndex: 0, completed: false, jiraKey: 'PM-214', storyPoints: 8, labels: ['frontend', 'mobile'] },
      { title: 'Bulk card operations', description: 'Select-all and batch move/delete for cards', priority: 'low', dueInDays: -3, assigneeIndices: [1], columnIndex: 4, completed: true, jiraKey: 'ENG-1031', storyPoints: 3, labels: ['frontend'] }
    ]
  },
  {
    title: 'PM × Sales',
    description: 'Product features driven by sales pipeline and customer deals',
    columns: [{ title: 'Requested' }, { title: 'Evaluating' }, { title: 'Roadmap' }, { title: 'In Dev' }, { title: 'Delivered' }],
    teamMembers: [
      { name: 'Sam Wells', initials: 'SW', color: 'bg-teal-500' },
      { name: 'Olivia Grant', initials: 'OG', color: 'bg-red-500' },
      { name: 'Diego Torres', initials: 'DT', color: 'bg-yellow-500' }
    ],
    cards: [
      { title: 'CRM integration', description: 'Bi-directional sync of contacts, deals, and tasks', priority: 'high', dueInDays: 19, assigneeIndices: [2], columnIndex: 3, completed: false, jiraKey: 'PM-219', storyPoints: 13, labels: ['integration', 'crm'] },
      { title: 'Custom pricing tier builder', description: 'Self-serve UI for enterprise pricing configuration', priority: 'high', dueInDays: 38, assigneeIndices: [0], columnIndex: 2, completed: false, jiraKey: 'SALES-88', storyPoints: 8, labels: ['billing', 'enterprise'] },
      { title: 'White-label branding', description: 'Custom logos, colours, and domain for enterprise clients', priority: 'medium', dueInDays: 28, assigneeIndices: [1], columnIndex: 1, completed: false, jiraKey: 'SALES-91', storyPoints: 5, labels: ['enterprise', 'ui'] },
      { title: 'Usage analytics dashboard', description: 'Per-seat and per-feature usage metrics for admins', priority: 'medium', dueInDays: -1, assigneeIndices: [0, 2], columnIndex: 4, completed: true, jiraKey: 'PM-196', storyPoints: 8, labels: ['analytics', 'admin'] },
      { title: 'API rate limit increase', description: 'Raise default limits for enterprise plan customers', priority: 'low', dueInDays: 49, assigneeIndices: [1], columnIndex: 0, completed: false, jiraKey: 'ENG-1078', storyPoints: 2, labels: ['api', 'enterprise'] }
    ]
  },
  {
    title: 'PM × Legal',
    description: 'Compliance, privacy, and legal requirements across the product',
    columns: [{ title: 'Under Review' }, { title: 'Legal Feedback' }, { title: 'Implementation' }, { title: 'Compliance Check' }, { title: 'Closed' }],
    teamMembers: [
      { name: 'Chloe Martin', initials: 'CM', color: 'bg-pink-500' },
      { name: 'James Wu', initials: 'JW', color: 'bg-green-500' },
      { name: 'Rina Patel', initials: 'RP', color: 'bg-purple-500' }
    ],
    cards: [
      { title: 'GDPR data retention policy', description: 'Auto-delete inactive accounts after 24 months', priority: 'high', dueInDays: 12, assigneeIndices: [2], columnIndex: 2, completed: false, jiraKey: 'LEGAL-44', storyPoints: 8, labels: ['gdpr', 'compliance', 'data'], blockedByIndices: [1] },
      { title: 'Terms of Service update', description: 'Reflect new SaaS data processing addendum', priority: 'high', dueInDays: -1, assigneeIndices: [0], columnIndex: 1, completed: false, jiraKey: 'LEGAL-41', storyPoints: 3, labels: ['legal', 'tos'] },
      { title: 'SOC 2 Type II audit prep', description: 'Evidence collection and gap remediation for annual audit', priority: 'high', dueInDays: 48, assigneeIndices: [1], columnIndex: 0, completed: false, jiraKey: 'LEGAL-48', storyPoints: 13, labels: ['compliance', 'audit'] },
      { title: 'Cookie consent banner', description: 'PECR-compliant opt-in/opt-out flow', priority: 'medium', dueInDays: -5, assigneeIndices: [2], columnIndex: 4, completed: true, jiraKey: 'LEGAL-37', storyPoints: 5, labels: ['gdpr', 'frontend'] },
      { title: 'CCPA compliance review', description: 'Data mapping and "do not sell" request flow', priority: 'medium', dueInDays: 33, assigneeIndices: [0, 1], columnIndex: 3, completed: false, jiraKey: 'LEGAL-51', storyPoints: 8, labels: ['ccpa', 'compliance'] }
    ]
  },
  {
    title: 'PM × Marketing',
    description: 'Product positioning, launches, and growth collaboration',
    columns: [{ title: 'Ideation' }, { title: 'Brief' }, { title: 'In Progress' }, { title: 'Review' }, { title: 'Launched' }],
    teamMembers: [
      { name: 'Nina Fox', initials: 'NF', color: 'bg-orange-500' },
      { name: 'Carlos Ruiz', initials: 'CR', color: 'bg-blue-500' },
      { name: 'Aisha Diallo', initials: 'AD', color: 'bg-teal-500' }
    ],
    cards: [
      { title: 'Redesign pricing page', description: 'A/B test new feature-comparison layout', priority: 'high', dueInDays: 12, assigneeIndices: [0], columnIndex: 3, completed: false, jiraKey: 'MKT-122', storyPoints: 5, labels: ['web', 'conversion'] },
      { title: 'Case study: Acme Corp', description: '2× ARR growth story with quotes and metrics', priority: 'medium', dueInDays: 23, assigneeIndices: [1], columnIndex: 2, completed: false, jiraKey: 'MKT-128', storyPoints: 3, labels: ['content', 'customers'] },
      { title: 'Product launch webinar', description: 'Live demo and Q&A for Q2 feature release', priority: 'high', dueInDays: 2, assigneeIndices: [2], columnIndex: 4, completed: true, jiraKey: 'MKT-115', storyPoints: 5, labels: ['events', 'launch'] },
      { title: 'Feature announcement email', description: '3-email drip for new board analytics feature', priority: 'medium', dueInDays: 17, assigneeIndices: [1, 2], columnIndex: 1, completed: false, jiraKey: 'MKT-133', storyPoints: 2, labels: ['email', 'launch'] },
      { title: 'In-app onboarding tour', description: 'Interactive walkthrough for new user activation', priority: 'medium', dueInDays: 38, assigneeIndices: [0], columnIndex: 0, completed: false, jiraKey: 'PM-222', storyPoints: 8, labels: ['onboarding', 'ux'] }
    ]
  },
  {
    title: 'PM × PM',
    description: 'Cross-PM sync, roadmap alignment, and planning ceremonies',
    columns: [{ title: 'Backlog' }, { title: 'This Sprint' }, { title: 'In Review' }, { title: 'Done' }],
    teamMembers: [
      { name: 'Taylor Reyes', initials: 'TR', color: 'bg-indigo-500' },
      { name: 'Morgan Blake', initials: 'MB', color: 'bg-pink-500' },
      { name: 'Sam Wells', initials: 'SW', color: 'bg-teal-500' }
    ],
    cards: [
      { title: 'Q3 roadmap prioritisation', description: 'Force-rank top 15 features with stakeholder input', priority: 'high', dueInDays: 9, assigneeIndices: [0, 1], columnIndex: 2, completed: false, jiraKey: 'PM-231', storyPoints: 5, labels: ['planning', 'roadmap'] },
      { title: 'Stakeholder alignment session', description: 'Monthly all-hands product review with GTM leads', priority: 'medium', dueInDays: 7, assigneeIndices: [2], columnIndex: 1, completed: false, jiraKey: 'PM-235', storyPoints: 2, labels: ['planning'] },
      { title: 'Customer feedback synthesis', description: 'Cluster last 90 days of NPS verbatims by theme', priority: 'medium', dueInDays: 14, assigneeIndices: [1], columnIndex: 1, completed: false, jiraKey: 'PM-238', storyPoints: 3, labels: ['research', 'data'] },
      { title: 'Product metrics review', description: 'WAU, feature adoption, retention cohorts', priority: 'low', dueInDays: 19, assigneeIndices: [0], columnIndex: 0, completed: false, jiraKey: 'PM-242', storyPoints: 2, labels: ['analytics'] },
      { title: 'OKR mid-quarter check-in', description: 'Progress review and re-forecast for Q2 OKRs', priority: 'high', dueInDays: 1, assigneeIndices: [2], columnIndex: 3, completed: true, jiraKey: 'PM-224', storyPoints: 3, labels: ['okr', 'planning'] }
    ]
  },
  {
    title: 'Sales Pipeline',
    description: 'Track and manage sales opportunities through every stage',
    columns: [{ title: 'Lead' }, { title: 'Qualified' }, { title: 'Proposal' }, { title: 'Negotiation' }, { title: 'Closed Won' }],
    teamMembers: [
      { name: 'Jordan Pierce', initials: 'JP', color: 'bg-blue-500' },
      { name: 'Sofia Nguyen', initials: 'SN', color: 'bg-red-500' },
      { name: 'Rafael Stone', initials: 'RS', color: 'bg-green-500' }
    ],
    cards: [
      { title: 'TechCorp Enterprise — $240k', description: 'Final legal review; procurement sign-off pending', priority: 'high', dueInDays: 15, assigneeIndices: [0], columnIndex: 3, completed: false, jiraKey: 'SALES-103', storyPoints: 5, labels: ['enterprise', 'legal-hold'] },
      { title: 'Zenith Labs pilot — $28k', description: 'Send revised proposal after discovery call', priority: 'medium', dueInDays: 23, assigneeIndices: [1], columnIndex: 2, completed: false, jiraKey: 'SALES-107', storyPoints: 3, labels: ['smb'] },
      { title: 'GlobeX initial outreach', description: 'Warm intro via a mutual contact; schedule first call', priority: 'low', dueInDays: 8, assigneeIndices: [2], columnIndex: 0, completed: false, jiraKey: 'SALES-111', storyPoints: 1, labels: ['outbound'] },
      { title: 'Apex Inc renewal — $96k', description: 'Upsell to growth plan with SSO add-on', priority: 'high', dueInDays: -3, assigneeIndices: [0, 1], columnIndex: 4, completed: true, jiraKey: 'SALES-96', storyPoints: 5, labels: ['renewal', 'enterprise'] },
      { title: 'Meridian startup package — $12k', description: 'Respond to security questionnaire', priority: 'medium', dueInDays: 30, assigneeIndices: [2], columnIndex: 1, completed: false, jiraKey: 'SALES-114', storyPoints: 2, labels: ['startup', 'security-review'] }
    ]
  },
  {
    title: 'Marketing Campaign',
    description: 'End-to-end campaign planning from concept to publication',
    columns: [{ title: 'Ideas' }, { title: 'Planning' }, { title: 'Creating' }, { title: 'Review' }, { title: 'Published' }],
    teamMembers: [
      { name: 'Nina Fox', initials: 'NF', color: 'bg-orange-500' },
      { name: 'Carlos Ruiz', initials: 'CR', color: 'bg-blue-500' },
      { name: 'Mia Torres', initials: 'MT', color: 'bg-purple-500' }
    ],
    cards: [
      { title: 'Summer product launch campaign', description: 'Multi-channel: email, social, paid ads, press', priority: 'high', dueInDays: 19, assigneeIndices: [0, 1], columnIndex: 2, completed: false, jiraKey: 'MKT-141', storyPoints: 13, labels: ['launch', 'multi-channel'] },
      { title: 'Sponsored social posts — Q2', description: '4-post series targeting engineering managers', priority: 'medium', dueInDays: 12, assigneeIndices: [2], columnIndex: 1, completed: false, jiraKey: 'MKT-145', storyPoints: 3, labels: ['social', 'paid'] },
      { title: 'Developer conference sponsorship', description: 'Booth, swag, and talk submission for Kestrel DevCon 2026', priority: 'high', dueInDays: 9, assigneeIndices: [1], columnIndex: 3, completed: false, jiraKey: 'MKT-138', storyPoints: 8, labels: ['events', 'dev'] },
      { title: 'Email nurture sequence', description: '5-part onboarding drip for trial signups', priority: 'medium', dueInDays: -1, assigneeIndices: [0], columnIndex: 4, completed: true, jiraKey: 'MKT-129', storyPoints: 5, labels: ['email', 'onboarding'] },
      { title: 'SEO content sprint', description: '8 long-form posts targeting high-intent keywords', priority: 'low', dueInDays: 38, assigneeIndices: [2], columnIndex: 0, completed: false, jiraKey: 'MKT-149', storyPoints: 8, labels: ['seo', 'content'] }
    ]
  },
  {
    title: 'Customer Success',
    description: 'Track customer health, onboarding milestones, and renewals',
    columns: [{ title: 'Onboarding' }, { title: 'Active' }, { title: 'At Risk' }, { title: 'Renewal Due' }, { title: 'Churned' }],
    teamMembers: [
      { name: 'Lena Park', initials: 'LP', color: 'bg-teal-500' },
      { name: 'Omar Hassan', initials: 'OH', color: 'bg-green-500' },
      { name: 'Bea Collins', initials: 'BC', color: 'bg-pink-500' }
    ],
    cards: [
      { title: 'Acme Corp — onboarding week 2', description: 'Complete data import and train 3 admin users', priority: 'high', dueInDays: 1, assigneeIndices: [0], columnIndex: 0, completed: false, jiraKey: 'PM-251', storyPoints: 5, labels: ['onboarding', 'enterprise'] },
      { title: 'GlobeX — health score drop', description: 'DAU fell 40% — schedule exec call and remediation plan', priority: 'high', dueInDays: -3, assigneeIndices: [1], columnIndex: 2, completed: false, jiraKey: 'PM-255', storyPoints: 8, labels: ['at-risk', 'churn'] },
      { title: 'Zenith Labs renewal', description: 'Annual contract renewal — propose 2-year deal', priority: 'medium', dueInDays: 19, assigneeIndices: [2], columnIndex: 3, completed: false, jiraKey: 'SALES-119', storyPoints: 3, labels: ['renewal'] },
      { title: 'TechCorp quarterly business review', description: 'Present ROI metrics and roadmap sneak peek', priority: 'low', dueInDays: 28, assigneeIndices: [0, 1], columnIndex: 1, completed: false, jiraKey: 'PM-259', storyPoints: 2, labels: ['qbr', 'enterprise'] },
      { title: 'Apex Inc success plan', description: 'Define Q3 goals and adoption milestones', priority: 'medium', dueInDays: 17, assigneeIndices: [2], columnIndex: 1, completed: false, jiraKey: 'PM-263', storyPoints: 3, labels: ['success-plan'] }
    ]
  },
  {
    title: 'OKR & Strategy',
    description: 'Company objectives, key results, and strategic initiatives',
    columns: [{ title: 'Planning' }, { title: 'In Progress' }, { title: 'On Track' }, { title: 'Off Track' }, { title: 'Achieved' }],
    teamMembers: [
      { name: 'Alex Kim', initials: 'AK', color: 'bg-blue-500' },
      { name: 'Taylor Reyes', initials: 'TR', color: 'bg-indigo-500' },
      { name: 'Sofia Nguyen', initials: 'SN', color: 'bg-red-500' }
    ],
    cards: [
      { title: 'Reach $5M ARR by Q3', description: 'Requires 22 new enterprise logos and 15% expansion from existing', priority: 'high', dueInDays: 140, assigneeIndices: [0, 1], columnIndex: 1, completed: false, jiraKey: 'PM-271', storyPoints: 13, labels: ['revenue', 'okr'] },
      { title: 'Launch enterprise tier', description: 'SSO, audit logs, SLA, and dedicated CSM', priority: 'high', dueInDays: 49, assigneeIndices: [1], columnIndex: 1, completed: false, jiraKey: 'ENG-1084', storyPoints: 21, labels: ['enterprise', 'okr'] },
      { title: 'Reduce churn below 5%', description: 'Invest in CS tooling, early-warning system, and playbooks', priority: 'high', dueInDays: 140, assigneeIndices: [2], columnIndex: 2, completed: false, jiraKey: 'PM-274', storyPoints: 8, labels: ['retention', 'okr'] },
      { title: 'Grow engineering team to 25', description: '8 hires needed — sourcing in progress, 3 offers pending', priority: 'medium', dueInDays: 80, assigneeIndices: [0], columnIndex: 3, completed: false, jiraKey: 'ENG-1089', storyPoints: 5, labels: ['hiring', 'okr'] },
      { title: 'Achieve NPS above 50', description: 'Baseline 42 — improve via onboarding redesign', priority: 'medium', dueInDays: 140, assigneeIndices: [1, 2], columnIndex: 1, completed: false, jiraKey: 'PM-277', storyPoints: 8, labels: ['nps', 'okr'] },
      { title: 'Expand to EU market', description: 'GDPR compliance, EU hosting, and local GTM motion', priority: 'high', dueInDays: 232, assigneeIndices: [1], columnIndex: 0, completed: false, jiraKey: 'PM-281', storyPoints: 21, labels: ['expansion', 'okr', 'gdpr'] }
    ]
  }
];
