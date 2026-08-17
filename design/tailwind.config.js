/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Primary palette
        primary: {
          DEFAULT: '#0F6B6D',
          light: '#3F8C8D',
          dark: '#0C5658',
          foreground: '#ffffff', // Text on primary buttons
        },
        // Secondary palette
        secondary: {
          DEFAULT: '#6E7758',
          light: '#8B9276',
          dark: '#585F46',
          foreground: '#ffffff',
        },
        // Accent for CTAs and highlights
        accent: {
          DEFAULT: '#C06C3D',
          foreground: '#ffffff',
        },
        // Backgrounds
        background: {
          DEFAULT: '#F2EFE8',
          alt: '#E7E1D7',
        },
        surface: '#FAF8F3',
        // Text colors
        foreground: {
          DEFAULT: '#1D2326',
          muted: '#707873',
        },
        // Borders
        border: {
          DEFAULT: '#CBC4B8',
          light: '#E1DBD1',
        },
        // Input specific
        input: {
          DEFAULT: '#CBC4B8',
          focus: '#0F6B6D',
        },
        // Ring (focus outline)
        ring: {
          DEFAULT: '#0F6B6D',
        },
        // Semantic colors
        success: {
          DEFAULT: '#5E765B',
          light: '#5E765B20',
          foreground: '#ffffff',
        },
        warning: {
          DEFAULT: '#B78324',
          light: '#B7832420',
          foreground: '#000000',
        },
        error: {
          DEFAULT: '#9A6046',
          light: '#9A604620',
          foreground: '#ffffff',
        },
        info: {
          DEFAULT: '#0F6B6D',
          light: '#0F6B6D20',
          foreground: '#ffffff',
        },
        // Destructive alias
        destructive: {
          DEFAULT: '#9A6046',
          foreground: '#ffffff',
        },
        // Muted for disabled states
        muted: {
          DEFAULT: '#E7E1D7',
          foreground: '#707873',
        },
        // Card
        card: {
          DEFAULT: '#FAF8F3',
          foreground: '#1D2326',
        },
        // Popover
        popover: {
          DEFAULT: '#FAF8F3',
          foreground: '#1D2326',
        },
      },
      // Text color defaults
      textColor: {
        DEFAULT: '#1D2326',
      },
      // Background color defaults
      backgroundColor: {
        DEFAULT: '#F2EFE8',
      },
      // Border color defaults
      borderColor: {
        DEFAULT: '#CBC4B8',
      },
      // Placeholder color
      placeholderColor: {
        DEFAULT: '#707873',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        heading: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'monospace'],
      },
      fontWeight: {
        body: '400',
        heading: '700',
      },
      lineHeight: {
        body: '1.6',
        heading: '1.2',
      },
      spacing: {
        'base': '8px',
        '4': '4px',
        '8': '8px',
        '12': '12px',
        '16': '16px',
        '24': '24px',
        '32': '32px',
        '48': '48px',
        '64': '64px',
        '96': '96px',
      },
      borderRadius: {
        none: '0',
        sm: '4px',
        DEFAULT: '8px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        '2xl': '24px',
        full: '9999px',
      },
      boxShadow: {
        none: 'none',
        sm: '0 1px 2px rgba(0, 0, 0, 0.04)',
        DEFAULT: '0 2px 4px rgba(0, 0, 0, 0.06)',
        md: '0 2px 4px rgba(0, 0, 0, 0.06)',
        lg: '0 4px 8px rgba(0, 0, 0, 0.08)',
        xl: '0 8px 16px rgba(0, 0, 0, 0.1)',
      },
      // Ring width for focus states
      ringWidth: {
        DEFAULT: '2px',
      },
      ringColor: {
        DEFAULT: '#0F6B6D',
      },
      ringOffsetWidth: {
        DEFAULT: '2px',
      },
      ringOffsetColor: {
        DEFAULT: '#F2EFE8',
      },
      // Animation
      animation: {
        'spin-slow': 'spin 2s linear infinite',
        'pulse-slow': 'pulse 3s ease-in-out infinite',
      },
      // Transition
      transitionDuration: {
        DEFAULT: '200ms',
      },
    },
  },
  plugins: [
    // Uncomment if using forms:
    // require('@tailwindcss/forms'),
  ],
}

/*
 * USAGE EXAMPLES:
 *
 * Buttons:
 *   <button class="bg-primary text-primary-foreground hover:bg-primary-dark">
 *   <button class="bg-secondary text-secondary-foreground">
 *   <button class="border border-border bg-transparent hover:bg-muted">
 *   <button class="bg-destructive text-destructive-foreground">
 *
 * Inputs:
 *   <input class="border-input bg-background text-foreground placeholder:text-foreground-muted focus:ring-ring">
 *
 * Cards:
 *   <div class="bg-card text-card-foreground border border-border rounded shadow">
 *
 * Alerts:
 *   <div class="bg-success-light text-success border-l-4 border-success">
 *   <div class="bg-error-light text-error border-l-4 border-error">
 *
 * Focus states:
 *   <button class="focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2">
 */
