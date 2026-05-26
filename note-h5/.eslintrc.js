module.exports = {
    env: {
        browser: true,
        es6: true,
        node: true,
    },
    settings: {
        // 自动发现 React 的版本，从而进行规范 react 代码
        react: {
            pragma: 'React',
            version: 'detect',
        },
    },
    parser: '@typescript-eslint/parser',
    parserOptions: {
        ecmaVersion: 2020,
        sourceType: 'module',
        ecmaFeatures: {
            jsx: true,
        },
    },
    plugins: ['@typescript-eslint', 'prettier', 'react-hooks'],
    extends: [
        'eslint:recommended',
        'plugin:react/recommended',
        'plugin:@typescript-eslint/recommended',
        'plugin:react-hooks/recommended',
        'plugin:prettier/recommended',
    ],
    rules: {
        semi: 'off',
        'arrow-parens': 'off',
        'prettier/prettier': 'warn',
        'no-console': 'warn',
        'operator-linebreak': [
            'error',
            'before',
            {
                overrides: {
                    '?': 'before',
                    ':': 'before',
                    '&&': 'before',
                    '+': 'before',
                },
            },
        ],
        'react/react-in-jsx-scope': 'off',
        '@typescript-eslint/no-explicit-any': 'warn',
        '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    },
};
