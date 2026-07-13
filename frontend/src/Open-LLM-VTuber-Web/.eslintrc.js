module.exports = {
  extends: [
    '@electron-toolkit/eslint-config-ts',
    'plugin:react/recommended',
  ],
  plugins: ['@typescript-eslint', 'react'],
  settings: {
    react: {
      version: 'detect',
    },
  },
  rules: {
    'no-unused-vars': 'off',
    'max-len': 'off',
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-unused-vars': 'off',
    'no-console': 'off',
    'react/jsx-filename-extension': [1, { extensions: ['.tsx', '.jsx'] }],
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    'react/jsx-props-no-spreading': 'off',
    quotes: 'off',
    'operator-linebreak': 'off',
    'react/display-name': 'off',
    'consistent-return': 'off',
    'object-curly-newline': 'off',
    'react/require-default-props': 'off',
  },
};
