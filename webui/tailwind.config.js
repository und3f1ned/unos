/**
 * A colour token: the CSS variable --color-<name> of src/app.css, the red, green and blue of the
 * current theme, with Tailwind's opacity modifier (bg-surface/50). `opacity` is the token's own,
 * multiplied by the modifier's.
 */
function token(name, opacity) {
	return opacity === undefined
		? `rgb(var(--color-${name}) / <alpha-value>)`
		: `rgb(var(--color-${name}) / calc(<alpha-value> * ${opacity}))`;
}

/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			// The colour tokens (ADR-0012, ADR-0400). Each is a CSS variable of src/app.css with a value
			// for the dark and one for the light theme (ADR-0402): a page that uses only the tokens
			// follows the theme. No colour is written here; supervisor/tests/test_webui_theme.py checks
			// that both themes give every token a value and meet WCAG AA.
			colors: {
				// Background colors
				surface: {
					DEFAULT: token('surface'), // main background
					elevated: token('surface-elevated'), // cards, modals
					overlay: token('surface-overlay'), // fields, secondary buttons, hover states, nested
					hover: token('surface-hover'), // hover over overlay
					muted: token('surface-muted', 0.5) // subtle backgrounds (nested cards)
				},
				// Border colors
				border: {
					DEFAULT: token('border'),
					strong: token('border-strong'), // field borders
					muted: token('border-muted', 0.5)
				},
				// Text colors
				content: {
					DEFAULT: token('content'), // primary text
					secondary: token('content-secondary'), // secondary text
					muted: token('content-muted'), // hints, placeholders
					soft: token('content-soft'), // between primary and secondary
					inverse: token('content-inverse'), // on a background of the other theme
					'on-brand': token('content-on-brand') // on brand and other solid fills
				},
				// Accent colors (for icons and highlights)
				accent: {
					cpu: token('accent-cpu'),
					memory: token('accent-memory'),
					network: token('accent-network'),
					storage: token('accent-storage'),
					container: token('accent-container'),
					temperature: token('accent-temperature')
				},
				// Brand (primary actions, selected tabs, focus)
				brand: {
					DEFAULT: token('brand'),
					hover: token('brand-hover'),
					ring: token('brand-ring'), // focus and selection lines
					disabled: token('brand-disabled')
				},
				// Semantic status colors
				status: {
					success: token('status-success'),
					warning: token('status-warning'),
					error: token('status-error'),
					info: token('status-info'),
					neutral: token('status-neutral') // off, unknown
				},
				// Solid fills with content-on-brand text: the variants of Button
				solid: {
					success: token('solid-success'),
					'success-hover': token('solid-success-hover'),
					warning: token('solid-warning'),
					'warning-hover': token('solid-warning-hover'),
					error: token('solid-error'),
					'error-hover': token('solid-error-hover')
				}
			},
			// A border or a ring without a colour of its own takes the theme's, not Tailwind's gray-200 and
			// blue-500.
			borderColor: {
				DEFAULT: token('border')
			},
			ringColor: {
				DEFAULT: token('brand-ring')
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
