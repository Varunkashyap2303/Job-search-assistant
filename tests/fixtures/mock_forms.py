"""Local stand-ins for the Greenhouse, Lever and Ashby application forms, mirroring the DOM structure
observed on the real hosted pages (ids/names, comboboxes, Yes/No buttons). Each page sets
window.__submitted = true if its Submit button is ever clicked, so tests can prove the agent never submits."""

COMBOBOX_JS = """
<script>
document.querySelectorAll('[data-options]').forEach(input => {
  const list = document.createElement('div'); list.setAttribute('role', 'listbox'); list.style.display = 'none';
  input.after(list);
  const render = () => {
    list.innerHTML = '';
    input.dataset.options.split(';;').filter(o => o.toLowerCase().includes(input.value.toLowerCase())).forEach(o => {
      const opt = document.createElement('div'); opt.setAttribute('role', 'option'); opt.textContent = o;
      opt.onclick = () => { input.value = o; input.dataset.chosen = (input.dataset.chosen ? input.dataset.chosen + '|' : '') + o; list.style.display = 'none'; };
      list.appendChild(opt);
    });
    list.style.display = 'block';
  };
  input.addEventListener('focus', render); input.addEventListener('input', render);
});
window.__submitted = false;
document.querySelectorAll('button.submit').forEach(b => b.onclick = (e) => { e.preventDefault(); window.__submitted = true; });
</script>"""

GREENHOUSE = """<html><body><form>
<label for="first_name">First Name*</label><input type="text" id="first_name">
<label for="last_name">Last Name*</label><input type="text" id="last_name">
<label for="email">Email*</label><input type="text" id="email">
<label for="phone">Phone*</label><input type="tel" id="phone">
<label for="candidate-location">Location (City)*</label><input type="text" id="candidate-location" role="combobox" data-options="Melbourne VIC, Australia;;Melbourne, FL, USA;;Sydney NSW, Australia">
<label for="resume">Resume</label><input type="file" id="resume">
<label for="question_1">LinkedIn Profile*</label><input type="text" id="question_1">
<label for="question_2">Will you require sponsorship?*</label><input type="text" id="question_2" role="combobox" data-options="Yes;;No">
<label for="136">Gender identity</label><input type="text" id="136" role="combobox" data-options="Man;;Woman;;I don't wish to answer">
<button class="submit" type="submit">Submit application</button>
</form>""" + COMBOBOX_JS + "</body></html>"

LEVER = """<html><body><form>
<ul><li class="application-question"><div class="application-label">Full name</div><input type="text" name="name" required></li>
<li class="application-question"><div class="application-label">Email</div><input type="email" name="email" required></li>
<li class="application-question"><div class="application-label">Resume/CV</div><input type="file" name="resume"></li>
<li class="application-question"><div class="application-label">LinkedIn URL</div><input type="text" name="urls[LinkedIn]"></li>
<li class="application-question custom-question"><div class="application-label">Authorised to work in Australia?</div>
  <select name="cards[a][field0]" required><option value="">Select...</option><option value="Yes">Yes</option><option value="No">No</option></select></li>
<li class="application-question custom-question"><div class="application-label">Notice period</div>
  <label><input type="radio" name="cards[a][field1]" value="None">None</label>
  <label><input type="radio" name="cards[a][field1]" value="3-4 weeks">3-4 weeks</label></li>
<li class="application-question"><div class="application-label">Ethnicity</div>
  <label><input type="checkbox" name="surveysResponses[x]" value="Asian">Asian</label>
  <label><input type="checkbox" name="surveysResponses[x]" value="Prefer not to say">Prefer not to say</label></li>
<li class="application-question custom-question"><div class="application-label">Anything else?</div><textarea name="cards[a][field2]"></textarea></li>
</ul><button class="submit" type="submit">Submit application</button></form>""" + COMBOBOX_JS + "</body></html>"

ASHBY = """<html><body><form>
<label for="_systemfield_name">Name</label><input type="text" id="_systemfield_name" name="_systemfield_name">
<label for="_systemfield_email">Email</label><input type="email" id="_systemfield_email" name="_systemfield_email">
<div class="field"><label>Do you require visa sponsorship?</label>
  <div><button type="button" onclick="this.parentElement.dataset.v='Yes'">Yes</button><button type="button" onclick="this.parentElement.dataset.v='No'">No</button>
  <input type="checkbox" name="visa-q" style="display:none"></div></div>
<label for="_systemfield_resume">Resume</label><input type="file" id="_systemfield_resume">
<label for="why">Tell us about AI at work</label><textarea id="why" name="why"></textarea>
<button class="submit" type="submit">Submit Application</button>
</form>""" + COMBOBOX_JS + "</body></html>"
