// ESLint for the Data Variance frontend (code review M-44). The rules that matter most for
// this codebase are the hook rules: exhaustive-deps would have caught the effect that
// re-ran on object identity (M-25).
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'no-unused-vars': ['warn', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_', caughtErrors: 'none' }],
    },
  },
  {
    files: ['vite.config.js', 'csp.js', 'proxy.js', 'eslint.config.js', 'tests/**'],
    languageOptions: { globals: { ...globals.node, ...globals.browser, ...globals.vitest } },
  },
]
