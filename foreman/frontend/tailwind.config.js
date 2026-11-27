/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        // Brand palette
        brand: {
          50:  '#f0f0ff',
          100: '#e4e2ff',
          200: '#ccc9ff',
          300: '#aba5ff',
          400: '#8577ff',
          500: '#6550f6',
          600: '#5232ec',
          700: '#4625d8',
          800: '#3a1fb5',
          900: '#321c90',
          950: '#1e1060',
        },
        // Surface palette (dark UI)
        surface: {
          950: '#090914',
          900: '#0f0f1e',
          800: '#16162b',
          700: '#1e1e38',
          600: '#262645',
          500: '#31315a',
        },
        // Status
        success: { 400: '#34d399', 500: '#10b981', 900: '#064e3b' },
        warning: { 400: '#fbbf24', 500: '#f59e0b', 900: '#451a03' },
        danger:  { 400: '#f87171', 500: '#ef4444', 900: '#450a0a' },
        info:    { 400: '#60a5fa', 500: '#3b82f6', 900: '#1e1b4b' },
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'mesh-dark': `
          radial-gradient(at 20% 30%, hsla(262,80%,20%,0.4) 0px, transparent 60%),
          radial-gradient(at 80% 10%, hsla(220,80%,20%,0.3) 0px, transparent 50%),
          radial-gradient(at 60% 80%, hsla(280,60%,15%,0.4) 0px, transparent 60%)
        `,
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'slide-in': 'slideIn 0.2s ease-out',
        'fade-in': 'fadeIn 0.3s ease-out',
        'shimmer': 'shimmer 2s linear infinite',
      },
      keyframes: {
        slideIn: {
          from: { opacity: '0', transform: 'translateY(-8px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
        shimmer: {
          from: { backgroundPosition: '-200% 0' },
          to:   { backgroundPosition: '200% 0' },
        },
      },
      boxShadow: {
        'glow-brand': '0 0 20px rgba(101,80,246,0.3)',
        'glow-success': '0 0 20px rgba(16,185,129,0.25)',
        'glow-danger': '0 0 20px rgba(239,68,68,0.25)',
        'card': '0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.5)',
        'card-hover': '0 4px 20px rgba(0,0,0,0.5), 0 1px 4px rgba(0,0,0,0.6)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
