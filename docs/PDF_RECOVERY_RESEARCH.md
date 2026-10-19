# Έρευνα δεύτερης προσπάθειας λήψης PDF

Ημερομηνία έρευνας: **2026-10-19**.

Η προτεινόμενη σειρά είναι: πρωτότυπο PDF από τον πάροχο, δεύτερη προσπάθεια
μέσα από τη φορτωμένη προβολή του παραστατικού, εκτυπωμένο αντίγραφο μόνο όπου
υπάρχει κατάλληλη προβολή και, αργότερα, προαιρετικό OCR για αρχειοθέτηση.
Τα παρακάτω ευρήματα συλλέχθηκαν πριν από την υλοποίηση της v0.9. Η τελική
λειτουργία και οι οδηγίες χρήσης περιγράφονται στο README.

## Τι μπορεί να πετύχει κάθε βήμα

- **Λήψη πρωτοτύπου:** προτιμάται το πραγματικό PDF που παρέχεται ως response,
  download ή blob στον browser. Διατηρούνται τα αρχικά bytes του αρχείου.
- **Δεύτερη προσπάθεια:** αφορά μόνο τις εκκρεμείς εγγραφές. Αν έχει φορτωθεί
  πραγματική HTML προβολή του τιμολογίου, μπορεί να αποθηκευτεί ως PDF μέσω
  εκτύπωσης. Το αποτέλεσμα καταγράφεται ως **εκτυπωμένο αντίγραφο**, όχι ως
  πρωτότυπο PDF του παρόχου. Η εκτύπωση της εξωτερικής σελίδας ενός PDF viewer
  δεν εγγυάται ότι περιλαμβάνει το ενσωματωμένο παραστατικό ή όλες τις σελίδες.
- **OCR:** διαβάζει εικόνα ή αρχείο που έχουμε ήδη. Δεν ανακτά περιεχόμενο που
  δεν έχει φορτώσει και δεν επιλύει CAPTCHA. Προηγείται εξαγωγή υπάρχοντος
  κειμένου· OCR χρειάζεται όταν το έγγραφο είναι εικόνα ή το κείμενο δεν επαρκεί.
- **Τελική αποτυχία:** παραμένει αποτυχία με σαφή αιτία όταν δεν ανακτηθεί
  παραστατικό. Δεν χαρακτηρίζεται επιτυχία επειδή εκτυπώθηκε μια σελίδα σφάλματος
  ή επειδή δημιουργήθηκε αρχείο με έγκυρη δομή PDF.

Δεν προτείνεται ενσωμάτωση solver ή παράκαμψη Cloudflare/CAPTCHA. Τυχόν
απαιτούμενος έλεγχος πρόσβασης ολοκληρώνεται από τον χρήστη στον browser.

## Παρόμοια προβλήματα εκτύπωσης και viewers

Το [Playwright `page.pdf()`](https://github.com/microsoft/playwright/blob/v1.56.1/docs/src/api/class-page.md#async-method-pagepdf)
χρησιμοποιεί print CSS και την εκτύπωση Chromium. Δεν είναι ισοδύναμο με το
κουμπί λήψης του ενσωματωμένου PDF viewer. Υπάρχουν αναφορές όπως το
[παλιότερο issue #6342](https://github.com/microsoft/playwright/issues/6342)
για PDF navigation σε headless Chromium. Το issue είναι ιστορικό και δεν
αποδεικνύει την αιτία των δύο σημερινών αποτυχιών στον υπολογιστή του χρήστη.

Στο PDF.js οι σελίδες συχνά φορτώνονται τμηματικά και αποδίδονται σε canvas.
Η [FAQ για όλες τις σελίδες και τα range requests](https://github.com/mozilla/pdf.js/wiki/Frequently-Asked-Questions)
και το [API `PDFDocumentProxy.getData()`](https://github.com/mozilla/pdf.js/blob/master/src/display/api.js)
οδηγούν στην προτίμηση ανάκτησης ολόκληρου του αρχικού αρχείου. Η εκτύπωση ή
το screenshot της εξωτερικής ιστοσελίδας μπορεί να δώσει κενή ή ελλιπή προβολή.
Η εφαρμογή ήδη επιχειρεί τις ρητές πηγές και τα διαθέσιμα αρχικά bytes πριν
από το νέο στάδιο εκτύπωσης HTML.

Το Playwright [δεν συνιστά το `networkidle` ως κριτήριο ετοιμότητας](https://github.com/microsoft/playwright/blob/main/docs/src/api/params.md#navigation-wait-until).
Οι [επιλογές εκτύπωσης Puppeteer](https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.pdfoptions.md)
δίνουν επίσης σημασία στη φόρτωση γραμματοσειρών. Για το δικό μας fallback
προτιμήθηκαν έλεγχοι ορατού παραστατικού, γραμματοσειρών/εικόνων, σταθερής
προβολής και του κειμένου στο παραγόμενο PDF. Δεν αποτελούν γενική εγγύηση
πληρότητας για κάθε άγνωστο viewer ή δυναμική ιστοσελίδα.

Playwright, Puppeteer και PDF.js χρησιμοποιούν Apache-2.0 για τον κώδικά τους.
Δεν ενσωματώθηκε νέος εξωτερικός PDF printer· χρησιμοποιείται η ήδη υπάρχουσα
δυνατότητα Chromium.

## Σύγκριση διαθέσιμων εργαλείων

| Εργαλείο | Άδεια και λειτουργία | Windows / εγκατάσταση | Συμπέρασμα για την εφαρμογή |
| --- | --- | --- | --- |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Apache-2.0, δωρεάν τοπικό OCR | Native binary και γλωσσικά δεδομένα· τα επίσημα docs παραπέμπουν στους installers UB Mannheim. Υποστηρίζονται ελληνικά `ell` και αγγλικά `eng`. | Λιτή βάση για μελλοντικό OCR ήδη αποθηκευμένων εγγράφων. |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | MPL-2.0· δημιουργεί αναζητήσιμο PDF χρησιμοποιώντας OCR | Python ≥3.11, native 64-bit Windows, εξωτερικό Tesseract. Οι λεπτομερείς οδηγίες επιτρέπουν pypdfium2 αντί Ghostscript για rasterization· ορισμένες διαδρομές PDF/A χρειάζονται Ghostscript. | Πρώτος υποψήφιος για προαιρετικά searchable αντίγραφα, μετά τη λήψη. |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Apache-2.0, δυνατότητα τοπικής λειτουργίας | Υποστηρίζει Windows και CPU/GPU με κατάλληλο inference engine και μοντέλα. Διαθέτει ελληνικό `el_PP-OCRv5_mobile_rec`. | Δεύτερος υποψήφιος για αξιολόγηση δύσκολων σαρώσεων· πιο σύνθετη εγκατάσταση. |
| [Surya](https://github.com/datalab-to/surya) | Κώδικας Apache-2.0, διαφορετική περιορισμένη άδεια μοντέλων | Το εξετασμένο README περιγράφει μοντέλο 650M και backend vLLM/GPU ή llama.cpp. Δεν επιβεβαιώθηκε απλή native Windows εγκατάσταση. | Δεν επιλέγεται αρχικά: βαρύτερη υποδομή και όχι καθολικά δωρεάν εμπορική χρήση. |
| [WeasyPrint](https://github.com/Kozea/WeasyPrint) | BSD-3-Clause, HTML/CSS → PDF | Windows executable ή Python ≥3.11 με Pango και αντίστοιχες βιβλιοθήκες | Δεν εκτελεί JavaScript ή αλληλεπίδραση χρήστη· δεν λύνει τη φόρτωση δυναμικών provider viewers. |
| [Gotenberg](https://github.com/gotenberg/gotenberg) | MIT για τον κώδικα, ξεχωριστές άδειες εξαρτήσεων και runtime | Docker-based API με Headless Chromium και LibreOffice | Ικανό εργαλείο εκτύπωσης αλλά προσθέτει container runtime χωρίς σαφές πλεονέκτημα εδώ. |

Οι άδειες των παραπάνω έργων δεν αντικαθιστούν τις άδειες των εξαρτήσεων ή των
μοντέλων τους. Ειδικά η [άδεια μοντέλων Surya](https://github.com/datalab-to/surya/blob/master/MODEL_LICENSE)
περιορίζει ορισμένες εμπορικές χρήσεις για οργανισμούς άνω των $5 εκατ. εσόδων ή
χρηματοδότησης. Δεν προκύπτει κατάταξη ακρίβειας για τα δικά μας ελληνικά
τιμολόγια από διαφορετικά benchmarks των έργων.

## Προτεινόμενη ελάχιστη προσέγγιση

Πρώτη επιλογή εκτύπωσης είναι το **Chromium που χρησιμοποιεί ήδη η εφαρμογή**.
Αποφεύγεται νέος PDF printer ή ξεχωριστός server, ενώ μπορεί να χρησιμοποιηθεί
η ίδια φορτωμένη συνεδρία. Αυτό είναι επιλογή υποδομής, όχι επιβεβαίωση ότι
εκτυπώνονται σωστά όλοι οι viewers. Χρειάζεται έλεγχος ότι έχει φορτωθεί το
πραγματικό παραστατικό, ότι περιλαμβάνονται όλες οι σελίδες και ότι δεν
αποθηκεύεται μήνυμα πρόσβασης ή κενή προβολή.

Η εφαρμογή πρέπει να διακρίνει την επιτυχή λήψη πρωτοτύπου, την αποθήκευση
εκτυπωμένου αντιγράφου και την αποτυχία. Τα metadata myDATA παραμένουν η πηγή
για πελάτη, προμηθευτή και MARK. Η αναγνώριση OCR δεν αλλάζει μόνη της αυτή την
αντιστοίχιση.

Για μελλοντική αρχειοθέτηση προτείνεται αρχικά Tesseract `ell+eng`, ενδεχομένως
μέσω OCRmyPDF, και αξιολόγηση σε αντιπροσωπευτικά ελληνικά δείγματα πριν από
επιλογή βαρύτερου μοντέλου. Τα πρωτότυπα διατηρούνται ανέπαφα· το αναγνωρισμένο
κείμενο/JSON αποθηκεύεται χωριστά ή δημιουργείται σαφώς διακριτό searchable
αντίγραφο. Το OCRmyPDF τεκμηριώνει ότι **δεν μπορεί να προσθέσει OCR και να
διατηρήσει έγκυρη υπάρχουσα ψηφιακή υπογραφή**. Δεν προτείνεται αυτόματη
ακύρωση υπογραφών.

## Πηγές και όρια επιβεβαίωσης

Διαβάστηκαν δημόσια πρωτογενή README, άδειες και docs από το
`raw.githubusercontent.com`. Οι παρακάτω σύνδεσμοι οδηγούν στα αντίστοιχα
αρχεία των έργων:

- [OCRmyPDF: εγκατάσταση](https://github.com/ocrmypdf/OCRmyPDF/blob/main/docs/installation.md),
  [Python/εξαρτήσεις](https://github.com/ocrmypdf/OCRmyPDF/blob/main/pyproject.toml),
  [άδεια](https://github.com/ocrmypdf/OCRmyPDF/blob/main/LICENSE),
  [cookbook και υπογραφές](https://github.com/ocrmypdf/OCRmyPDF/blob/main/docs/cookbook.md).
- [Tesseract: εγκατάσταση Windows](https://github.com/tesseract-ocr/tessdoc/blob/main/Installation.md),
  [γλώσσες, περιλαμβανομένου `ell`](https://github.com/tesseract-ocr/tessdoc/blob/main/Data-Files-in-different-versions.md),
  [άδεια](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE).
- [PaddleOCR: εγκατάσταση](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/installation.en.md),
  [ελληνικό μοντέλο](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md),
  [άδεια](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE).
- [Surya: README και απαιτήσεις backend](https://github.com/datalab-to/surya/blob/master/README.md),
  [άδεια κώδικα](https://github.com/datalab-to/surya/blob/master/LICENSE),
  [άδεια μοντέλων](https://github.com/datalab-to/surya/blob/master/MODEL_LICENSE).
- [WeasyPrint: χωρίς JavaScript/user interaction](https://github.com/Kozea/WeasyPrint/blob/main/docs/going_further.rst),
  [εγκατάσταση Windows](https://github.com/Kozea/WeasyPrint/blob/main/docs/first_steps.rst),
  [άδεια](https://github.com/Kozea/WeasyPrint/blob/main/LICENSE).
- [Gotenberg: δυνατότητες και Docker](https://github.com/gotenberg/gotenberg/blob/main/README.md),
  [άδεια](https://github.com/gotenberg/gotenberg/blob/main/LICENSE).

**Δεν δοκιμάστηκαν πραγματικά αιτήματα προς παρόχους, πραγματικά τιμολόγια,
OCR μοντέλα ή native Windows εκτέλεση.** Δεν εγκαταστάθηκαν εξαρτήσεις. Οι
σελίδες ReadTheDocs και το GitHub API εμποδίστηκαν από το δικτυακό proxy· οι
πηγές διαβάστηκαν από τα δημόσια raw αρχεία του GitHub. Το OCRmyPDF README
αναφέρει ακόμη υποχρεωτικό Ghostscript, ενώ οι λεπτομερείς οδηγίες το κάνουν
προαιρετικό σε συγκεκριμένες διαδρομές. Πριν από ενσωμάτωση χρειάζεται επιλογή
συγκεκριμένης έκδοσης και επαλήθευση εξαρτήσεων και λειτουργίας στα Windows.
