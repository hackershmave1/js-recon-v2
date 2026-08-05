export function updateCart() {
  const xhr = new XMLHttpRequest();
  xhr.open("PUT", "/api/v1/cart/42");
  xhr.send();
}
