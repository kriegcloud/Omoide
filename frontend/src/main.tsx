import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

import { AppThemeProvider } from './ThemeContext';

import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppThemeProvider>
      <App />
    </AppThemeProvider>
  </React.StrictMode>
);