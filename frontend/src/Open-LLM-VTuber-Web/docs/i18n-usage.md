# i18n (Internationalization) Usage Guide

This project uses i18next for multi-language support. It currently ships English only.

## Directory Structure

```
src/renderer/src/
├── i18n.ts                   # i18next configuration file
├── locales/                  # Translation files directory
│   └── en/                   # English translations
│       └── translation.json  # English translation file
```

## Usage

### Using translations in a component

```tsx
import { useTranslation } from 'react-i18next';

function MyComponent() {
  // Get the translation function and other i18n helpers
  const { t, i18n } = useTranslation();

  // Use the t function to translate text
  return (
    <div>
      <h1>{t('common.settings')}</h1>
      <p>{t('settings.general.language')}</p>

      {/* Change language */}
      <button onClick={() => i18n.changeLanguage('en')}>English</button>
    </div>
  );
}
```

### Adding new translations

1. Use `t('your.translation.key')` in your code to add a new translation key.
2. Run the scan command to extract translation keys:

```bash
npm run extract-translations
```

3. The extracted keys are written to `src/renderer/src/locales/en/translation.json`.
4. Edit the translation file and provide a value for each key.

## Translation Key Naming Convention

We organize translation keys using dot-separated namespaces, for example:

- `common.save` - the generic "Save" button text
- `settings.general.language` - the language setting on the settings page

## Nested Translations

For text containing variables, use interpolation:

```tsx
// In the translation file
{
  "welcome": "Welcome, {{name}}!"
}

// In the component
t('welcome', { name: 'John' }) // "Welcome, John!"
```

## Automatic Language Detection

The system automatically detects the user's browser language and uses the closest
available language. Users can also switch languages manually, and the choice is
saved in localStorage.
