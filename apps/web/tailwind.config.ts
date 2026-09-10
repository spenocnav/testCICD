import type { Config } from 'tailwindcss';
import tailwindcssAnimate from 'tailwindcss-animate';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  // `hover:` sólo aplica donde hay puntero que puede posarse. En pantallas
  // táctiles el hover se queda "pegado" tras el tap y ensucia el estado visual.
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      colors: {
        // Tokens de marca
        brand: {
          red: 'var(--brand-red)',
          black: 'var(--brand-black)',
          clear: 'var(--brand-clear)',
          gray: 'var(--brand-gray)',
        },
        accent: {
          lime: 'var(--accent-lime)',
          cream: 'var(--accent-cream)',
          yellow: 'var(--accent-yellow)',
          blue: 'var(--accent-blue)',
        },

        // Tokens semánticos (estilo shadcn)
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        card: {
          DEFAULT: 'var(--card)',
          foreground: 'var(--card-foreground)',
        },
        popover: {
          DEFAULT: 'var(--popover)',
          foreground: 'var(--popover-foreground)',
        },
        primary: {
          DEFAULT: 'var(--primary)',
          foreground: 'var(--primary-foreground)',
        },
        secondary: {
          DEFAULT: 'var(--secondary)',
          foreground: 'var(--secondary-foreground)',
        },
        muted: {
          DEFAULT: 'var(--muted)',
          foreground: 'var(--muted-foreground)',
        },
        destructive: {
          DEFAULT: 'var(--destructive)',
          foreground: 'var(--destructive-foreground)',
        },
        border: 'var(--border)',
        input: 'var(--input)',
        ring: 'var(--ring)',
        sidebar: {
          DEFAULT: 'var(--sidebar-background)',
          foreground: 'var(--sidebar-foreground)',
          border: 'var(--sidebar-border)',
          section: 'var(--sidebar-section)',
        },
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
        pill: 'var(--radius-pill)',
        DEFAULT: 'var(--radius)',
      },
      boxShadow: {
        soft: 'var(--shadow-soft)',
        md: 'var(--shadow-md)',
      },
      fontFamily: {
        sans: ['var(--font-manrope)', 'Segoe UI', 'Helvetica Neue', 'sans-serif'],
        heading: ['var(--font-raleway)', 'Segoe UI', 'sans-serif'],
      },
      backgroundImage: {
        'app-surface': 'linear-gradient(160deg, #F7F9F8 0%, #E9EDEB 55%, #E2E7E4 100%)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        'downtime-bar-grow': {
          from: { opacity: '0.35', transform: 'scaleX(0)' },
          to: { opacity: '1', transform: 'scaleX(1)' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        'downtime-bar-grow': 'downtime-bar-grow 500ms cubic-bezier(0.4, 0, 0.2, 1) both',
      },
    },
  },
  plugins: [tailwindcssAnimate],
};

export default config;
