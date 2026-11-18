/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			colors: {
				// Background colors
				surface: {
					DEFAULT: '#0f172a', // slate-900 - main background
					elevated: '#1e293b', // slate-800 - cards, modals
					overlay: '#334155', // slate-700 - hover states, nested
					hover: '#475569', // slate-600 - hover over overlay
					muted: 'rgba(51, 65, 85, 0.5)' // slate-700/50 - subtle backgrounds
				},
				// Border colors
				border: {
					DEFAULT: '#334155', // slate-700
					strong: '#475569', // slate-600 - field borders
					muted: 'rgba(51, 65, 85, 0.5)' // slate-700/50
				},
				// Text colors
				content: {
					DEFAULT: '#f1f5f9', // slate-100 - primary text
					secondary: '#94a3b8', // slate-400 - secondary text
					muted: '#64748b', // slate-500 - disabled/hint text
					soft: '#cbd5e1', // slate-300 - between primary and secondary
					inverse: '#0f172a', // slate-900 - on light backgrounds
					'on-brand': '#ffffff' // white - on brand and other solid fills
				},
				// Accent colors (for icons and highlights)
				accent: {
					cpu: '#60a5fa', // blue-400
					memory: '#c084fc', // purple-400
					network: '#4ade80', // green-400
					storage: '#60a5fa', // blue-400
					container: '#22d3ee', // cyan-400
					temperature: '#fb923c' // orange-400
				},
				// Brand (primary actions, selected tabs, focus)
				brand: {
					DEFAULT: '#2563eb', // blue-600
					hover: '#1d4ed8', // blue-700
					ring: '#3b82f6', // blue-500 - focus and selection lines
					disabled: '#1e40af' // blue-800
				},
				// Semantic status colors
				status: {
					success: '#4ade80', // green-400
					warning: '#facc15', // yellow-400
					error: '#f87171', // red-400
					info: '#60a5fa', // blue-400
					neutral: '#64748b' // slate-500 - off, unknown
				}
			},
			// Consistent spacing scale
			spacing: {
				'4.5': '1.125rem', // 18px
				'13': '3.25rem',
				'15': '3.75rem'
			},
			// Typography
			fontSize: {
				'2xs': ['0.625rem', { lineHeight: '0.75rem' }] // 10px
			},
			// Animation
			animation: {
				'spin-slow': 'spin 2s linear infinite'
			},
			// Border radius
			borderRadius: {
				DEFAULT: '0.5rem' // 8px - consistent radius
			}
		}
	},
	plugins: []
};
