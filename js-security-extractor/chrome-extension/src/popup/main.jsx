// main.jsx — Preact entry point. Mounts <App/> into #app and pulls in global styles
// (esbuild inlines styles.css into dist/popup.css via the css loader).
import { render } from 'preact';
import './styles.css';
import { App } from './app.jsx';

render(<App />, document.getElementById('app'));
