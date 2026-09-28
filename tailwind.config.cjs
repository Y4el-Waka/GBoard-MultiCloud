/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './static/dashboard.js',
    './static/metrics-history.js'
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        prtg: {
          up: '#7ad03a',
          down: '#dd3d36',
          warning: '#ffb700',
          paused: '#296E8A',
          acknowledged: '#7e57c2',
          unknown: '#90a4ae'
        }
      }
    }
  },
  plugins: []
};
