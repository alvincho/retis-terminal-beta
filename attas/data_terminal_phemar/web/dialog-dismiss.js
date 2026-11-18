// A modal dialog closes on a click outside its box only when the press and the release both happen outside it.
// A drag that starts inside (selecting text in one of its inputs) and ends outside makes the browser fire `click` on
// the dialog itself, at the release point outside its box; that is not a click outside.

/** Whether a pointer event on the dialog itself lands outside its box (on the backdrop). */
export function outsideBox(dialog,event){
 if(event.target!==dialog)return false;
 const box=dialog.getBoundingClientRect();
 return event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom;
}

/** Call `close` for a click whose press and release were both outside the dialog's box. */
export function closeOnOutsideClick(dialog,close){
 let pressedOutside=false;
 dialog.addEventListener('pointerdown',event=>{pressedOutside=outsideBox(dialog,event);});
 dialog.addEventListener('click',event=>{
  const pressed=pressedOutside;pressedOutside=false;
  if(pressed&&outsideBox(dialog,event))close();
 });
}
