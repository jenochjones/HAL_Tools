function addDataset(innerText, id) {
  // Create the outer div
  const div = document.createElement("div");
  div.className = "dataset";
  div.dataset.id = id;

  // Create the paragraph element
  const p = document.createElement("p");
  p.textContent = innerText;

  // Add the paragraph into the div
  div.appendChild(p);

  // Append the new dataset div to the container
  const container = document.getElementById("dataset-list");
  container.appendChild(div);
}

export default addDataset;