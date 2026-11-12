/** The closed failure categories a panel reports (phemacast/core/composition_resolution.py), as plain sentences. */
import {t} from './i18n.js';
const MESSAGES={
 'source-failed':'The data source could not answer. Try ↻ again later.',
 'local-source-unreachable':'Your local data source is not responding. Start it again, or disconnect it in Terminal settings → Connections to use Plaza’s source.',
 'local-pairing-rejected':'Your local data source no longer accepts this Terminal’s pairing. Disconnect it in Terminal settings → Connections, then pair it again.',
 'source-timeout':'The data source took too long. Try ↻ again.',
 'source-busy':'The data source is busy. Try ↻ again shortly.',
 'source-unavailable':'The data source does not offer this data.',
 'access-denied':'Your account has no access to this data.',
 'authentication-required':'Your Plaza sign-in expired or is being renewed. Press ↻ again in a few seconds; if it repeats, sign in again.',
 'request-rejected':'The data source rejected this request.',
 'input-rejected':'The data source could not use these inputs. Check the symbols.',
 'request-conflict':'The request changed while loading. Press ↻ to try again.',
 // The Terminal's own answer when a snapshot no longer matches its layout's definition.
 'Snapshot belongs to another Phema revision.':'This snapshot was saved from an older version of this layout. Open it with parameters, then save a new snapshot.'};
/** The message key for a closed failure category, or null for any other reason. */
export function failureMessage(reason){return typeof reason==='string'&&Object.hasOwn(MESSAGES,reason)?MESSAGES[reason]:null;}
/** Tooltip text: the sentence with the code kept for reports, or any other reason as it is. */
export function failureText(reason){const key=failureMessage(reason);return key?t(key)+' ('+reason+')':String(reason||'');}
/** A failed request as the viewer reads it: the Terminal's own transport failure in the interface language, saying Plaza is
 * still connected (the status bar shows Plaza, not each agent); any other answer as it came. */
export function requestFailure(detail){return detail==='Registered upstream is unavailable.'?t('An agent is temporarily unreachable. Plaza is still connected; try again in a moment.'):detail;}
