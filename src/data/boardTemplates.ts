import { Grid3X3, Layers, Briefcase, TrendingUp, ShoppingCart } from 'lucide-react';

export const BOARD_TEMPLATES = {
  simple: {
    name: 'Simple Kanban',
    icon: Layers,
    description: 'A basic kanban board with To Do, Doing, and Done columns',
    columns: [
      { id: 'todo', title: 'To Do' },
      { id: 'doing', title: 'Doing' },
      { id: 'done', title: 'Done' }
    ]
  },
  development: {
    name: 'Software Development',
    icon: Briefcase,
    description: 'Track development workflow from backlog to deployment',
    columns: [
      { id: 'backlog', title: 'Backlog' },
      { id: 'todo', title: 'To Do' },
      { id: 'inprogress', title: 'In Progress' },
      { id: 'testing', title: 'Testing' },
      { id: 'done', title: 'Done' }
    ]
  },
  marketing: {
    name: 'Marketing Campaign',
    icon: TrendingUp,
    description: 'Manage marketing campaigns from ideation to publication',
    columns: [
      { id: 'ideas', title: 'Ideas' },
      { id: 'planning', title: 'Planning' },
      { id: 'creating', title: 'Creating' },
      { id: 'review', title: 'Review' },
      { id: 'published', title: 'Published' }
    ]
  },
  sales: {
    name: 'Sales Pipeline',
    icon: ShoppingCart,
    description: 'Track sales opportunities through your pipeline',
    columns: [
      { id: 'leads', title: 'Leads' },
      { id: 'qualified', title: 'Qualified' },
      { id: 'proposal', title: 'Proposal' },
      { id: 'negotiation', title: 'Negotiation' },
      { id: 'closed', title: 'Closed' }
    ]
  },
  blank: {
    name: 'Start from Scratch',
    icon: Grid3X3,
    description: 'Create a custom board with your own columns',
    columns: [
      { id: 'col1', title: 'Column 1' },
      { id: 'col2', title: 'Column 2' },
      { id: 'col3', title: 'Column 3' }
    ]
  }
};
