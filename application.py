from flask import Flask, flash, json, jsonify, redirect, render_template, request, session
from flask_session import Session
from tempfile import mkdtemp
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from helpers import apology, login_required, lookup, get_time, send_email


app = Flask(__name__)

# Check for environment variable
if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is not set")

# Ensure responses aren't cached
@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database
engine = create_engine(os.getenv("DATABASE_URL"))
db = scoped_session(sessionmaker(bind=engine))


@app.route("/")
@login_required
def index():
    """Display index page"""

    return render_template("index.html")

@app.route("/search", methods=["GET"])
@login_required
def search():
    """Search for a book/author"""

    name = request.args.get("q").rstrip()
    if not name:
        return apology(message="please enter a name to search")
    search = db.execute("SELECT * FROM books WHERE title ILIKE :name OR author ILIKE :name OR isbn ILIKE :name", {"name": "%" + name + "%"}).fetchall()
    books = []
    if search:
        for result in search:
            book = {}
            book["title"] = result.title
            book["author"] = result.author
            book["year"] = result.year
            book["isbn"] = result.isbn
            books.append(book)
    return render_template("results.html", books=books)


@app.route("/books/<book>")
@login_required
def book_page(book):
    """Getting the info of the required book"""

    book = book.replace("_", " ")
    book_info = db.execute("SELECT * FROM books WHERE title = :book", {"book": book}).fetchone()
    book_info = dict(book_info)
    # Gettign data from Goodreads API
    gr_api = lookup(book_info["isbn"])
    if not gr_api:
        return apology("book not found on goodreads")
    # Adding Goodreads ratings to the dict containing info of the book
    book_info["average_rating"] = float(gr_api["average_rating"])
    book_info["ratings_count"] = gr_api["ratings_count"]
    # Checking if user wishlisted book before
    wishlisted_before = False
    q = db.execute("SELECT * FROM wishlist WHERE user_id = :u_id AND book_id = :b_id", {"u_id": session["user_id"], "b_id": book_info["id"]}).fetchall()
    if q:
        wishlisted_before = True
    # Getting users reviews
    reviews = db.execute("SELECT users.id, username, rating, review, reviews.time FROM reviews JOIN users ON reviews.user_id = users.id JOIN books ON reviews.book_id = books.id WHERE reviews.book_id = :book_id", {"book_id": book_info["id"]}).fetchall()
    user_reviewed_before = False
    for user in reviews:
        if user["id"] == session["user_id"]:
            user_reviewed_before = True
    return render_template("book_page.html", book=book_info, reviews=reviews, user_reviewed_before=user_reviewed_before, wishlisted_before=wishlisted_before)


@app.route("/authors/<author>")
@login_required
def author_page(author):
    """Display an author's page"""

    author = author.replace("_", " ")
    books = db.execute("SELECT title, isbn, year FROM books WHERE author = :author", {"author": author}).fetchall()
    return render_template("author.html", books=books, author=author)


@app.route("/authors")
@login_required
def authors():
    authors = db.execute("SELECT author, COUNT(*) FROM books GROUP BY author").fetchall()
    return render_template("authors.html", authors=authors)


@app.route("/review", methods=["POST"])
@login_required
def review():
    """Submit a review"""

    book_id = request.form.get("book_id")
    rating = request.form.get("rating")
    review = request.form.get("review")
    try:
        q = db.execute("SELECT * FROM reviews WHERE book_id = :book_id AND user_id = :user_id", {"book_id": book_id, "user_id": session["user_id"]}).fetchall()
        if q:
            return apology(message="you can only submit 1 review per book")
    except:
        return apology(message="something went wrong")

    if not rating or not review:
        return apology(message="please fill the form correctly")
    q = db.execute("SELECT * FROM wishlist WHERE user_id = :u_id AND book_id = :b_id",{"u_id": session["user_id"], "b_id": book_id}).fetchall()
    if q:
        db.execute("DELETE FROM wishlist WHERE user_id = :u_id AND book_id = :b_id",{"u_id": session["user_id"], "b_id": book_id})
        db.commit()
    book = db.execute("SELECT title FROM books WHERE id = :id", {"id": book_id}).fetchone()
    time = get_time()
    db.execute("INSERT INTO reviews(book_id, user_id, rating, review, time) VALUES(:book_id, :user_id, :rating, :review, :time)",
              {"book_id": book_id, "user_id": session["user_id"], "rating": rating, "review": review, "time": time})
    try:
        db.commit()
    except:
        return apology(message="something went wrong")
    flash("Review submitted!")
    return redirect(f'/books/{book[0].replace(" ", "_")}')


@app.route("/wishlist", methods=["GET", "POST"])
@login_required
def whishlist():
    """Display user wishlist"""

    if request.method == "GET":
        books = db.execute("SELECT books.title, books.isbn, wishlist.time FROM wishlist JOIN books ON wishlist.book_id = books.id WHERE user_id = :id", {"id": session["user_id"]}).fetchall()
        return render_template("wishlist.html", books=books)
    else:
        book_id = request.form.get("book_id")
        if not book_id:
            return apology(message="something went perfectly well, hacker!")
        q = db.execute("SELECT title FROM books WHERE id = :id", {"id": book_id}).fetchone()
        if not q:
            return apology(message="book not found in database, hacker!")
        q = db.execute("SELECT user_id, book_id FROM wishlist WHERE user_id = :id AND book_id = :book", {"id": session["user_id"], "book": book_id}).fetchall()
        in_wishlist = False
        if q:
            in_wishlist = True
        time = get_time()
        try:
            db.execute("INSERT INTO wishlist (user_id, book_id, time) VALUES(:user_id, :book_id, :time)", {"user_id": session["user_id"], "book_id": book_id, "time": time})
            db.commit()
        except:
            return apology(message="something went wrong in wishlist")
        flash("Book added to wishlist!")
        return redirect("/wishlist")


@app.route("/api/<isbn>")
@login_required
def api(isbn):
    """Create an API"""

    q = db.execute("SELECT * FROM books WHERE isbn = :isbn", {"isbn": isbn}).fetchone()
    if not q:
        return jsonify({"error": 404})
    gr_api = lookup(q.isbn)
    if not gr_api:
        return jsonify({"error": 404})
    book = dict(q)
    book["average_rating"] = float(gr_api["average_rating"])
    book["ratings_count"] = gr_api["ratings_count"]
    return jsonify(book)


@app.route("/profile")
@login_required
def profile():
    """Display user profile"""

    # Gettin all reviews by the users
    reviews = db.execute("SELECT books.isbn, title, rating, reviews.time FROM reviews JOIN users ON users.id = reviews.user_id JOIN books ON books.id = reviews.book_id WHERE users.id = :id", {"id": session["user_id"]}).fetchall()
    # Getting all number of whishlisted books
    wishlist_count = db.execute("SELECT COUNT(*) FROM wishlist WHERE user_id = :id", {"id": session["user_id"]}).fetchall()[0][0]
    # Selecting user email if any
    info = db.execute("SELECT time FROM users WHERE id = :id", {"id": session["user_id"]}).fetchone()
    time = info["time"]
    return render_template("profile.html", reviews=reviews, wishlist_count=wishlist_count, email=session["email"], time=time)


@app.route("/reviews")
@login_required
def reviews():
    """Display user reviews"""

    reviews = db.execute("SELECT books.isbn, title, rating, reviews.time FROM reviews JOIN users ON users.id = reviews.user_id JOIN books ON books.id = reviews.book_id WHERE users.id = :id", {"id": session["user_id"]}).fetchall()
    return render_template("reviews.html", reviews=reviews)


@app.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    """Get user feedback"""

    if request.method == "GET":
        return render_template("feedback.html")
    else:
        feedback_type = request.form.get("type")
        feedback = request.form.get("feedback")
        if not feedback_type or not feedback:
            return apology(message="please fill the form")
        db.execute("INSERT INTO user_feedback (id, feedback, type) VALUES(:id, :feedback, :type)", {"id": session["user_id"], "feedback": feedback, "type": feedback_type})
        db.commit()
        flash("Feedback submitted! Thanks for your feedback!")
        return redirect("/")


@app.route("/check", methods=["GET"])
def check():
    """Check if username or email is taken"""

    username = request.args.get("username")
    email = request.args.get("email")
    verify_username = db.execute("SELECT username FROM users WHERE username = :username", {"username": username}).fetchone()
    if email:
        verify_email = db.execute("SELECT email FROM users WHERE email = :email", {"email": email}).fetchone()
        if verify_email and verify_username:
            return jsonify("Username and email already taken.")
        if verify_username:
            return jsonify("Username already taken.")
        if verify_email:
            return jsonify("Email already taken.")
    if verify_username:
        return jsonify("Username already taken.")
    return jsonify(True)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    # Forget any user_id
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            return apology("must provide username", 403)

        # Ensure password was submitted
        elif not request.form.get("password"):
            return apology("must provide password", 403)

        # Query database for username
        rows = db.execute("SELECT * FROM users WHERE username = :username",
                          {"username": request.form.get("username")}).fetchone()

        # Ensure username exists and password is correct
        if not rows or not check_password_hash(rows["hash"], request.form.get("password")):
            return apology("invalid username and/or password", 403)

        # Remember which user has logged in
        session["user_id"] = rows["id"]
        session["username"] =  rows["username"]
        session["email"] = rows["email"]

        # Redirect user to home page
        return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/settings/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "GET":
        return render_template("change_password.html")
    else:
        password = request.form.get("password")
        new_password = request.form.get("new_password")
        confirmation = request.form.get("confirmation")
        pw_hash = db.execute("SELECT hash FROM users WHERE id = :id", {"id": session["user_id"]}).fetchone()["hash"]
        if not password or not new_password or new_password != confirmation:
            return apology("please fill the form correctly")
        elif password == new_password:
            return apology(message="new and old password can't be the same")
        elif not check_password_hash(pw_hash, password):
            return apology(message="incorrect password")
        else:
            # Specifications for password
            # password length
            if len(new_password) < 6:
                return apology(message="password must be longer than 6 characters")
            capital = None
            lower = None
            for c in new_password:
                if c.isupper():
                    capital = True
                if c.islower():
                    lower = True
            if not capital and not lower:
                return apology(message="password must contain atleast 1 uppercase and lowercase letter")
            # password must contain numbers
            if new_password.isalpha():
                return apology(message="password must contain numbers")
            # password must contain letters
            if new_password.isdigit():
                return apology(message="password must contain letters")
            db.execute("UPDATE users SET hash = :new_password WHERE id = :id",
                           {"new_password":generate_password_hash(new_password), "id": session["user_id"]})
            db.commit()
            flash("Password updated!")
            return redirect("/")


@app.route("/settings/change_email", methods=["GET", "POST"])
@login_required
def change_email():
    if request.method == "GET":
        return render_template("change_email.html")
    else:
        email = request.form.get("email")
        new_email = request.form.get("new_email")
        if not email or not new_email:
            return apology(message="please fill the form")
        emails = db.execute("SELECT email FROM users WHERE email = :email", {"email": new_email}).fetchone()
        if email != session["email"]:
            return apology(message="wrong email")
        if emails:
            return apology(message="email already taken")
        else:
            db.execute("UPDATE users SET email = :new_email WHERE id = :id",
                           {"new_email": new_email, "id": session["user_id"]})
            db.commit()
            session["email"] = new_email
            #message="Success!\n Your email was successfully changed!"
            #send_email(new_email, session["username"], message)
            flash("Email updated!")
            return redirect("/")


@app.route("/settings/add_email", methods=["GET", "POST"])
@login_required
def add_email():
    if request.method == "GET":
        return render_template("add_email.html")
    else:
        email = request.form.get("email")
        if not email:
            return apology(message="please enter an email")
        q = db.execute("SELECT email FROM users WHERE email = :email", {"email": email}).fetchone()
        if q:
            return apology(message="this email already exists")
        db.execute("UPDATE users SET email = :new_email WHERE id = :id",
                       {"new_email": email, "id": session["user_id"]})
        db.commit()
        #message="Success!\n Your email was successfully added to your account!"
        #send_email(email, session["username"], message)
        session["email"] = email
        flash("Email added!")
        return redirect("/")
@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")

@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""
    if request.method == "GET":
        return render_template("register.html")
    else:
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")
        email = request.form.get("email")
        if not username or not password or not confirmation or password != confirmation:
            return apology(message="please fill the form correctly to register.")
    # Checking for username
    c = db.execute("SELECT username FROM users WHERE username = :username", {"username": username}).fetchall()
    if c:
        return apology("username already taken")

    # Specifications for password

    # password length
    if len(password) < 6:
        return apology(message="password must be longer than 6 characters")
    # password must contain numbers
    if password.isalpha():
        return apology(message="password must contain numbers")
    # password must contain letters
    if password.isdigit():
        return apology(message="password must contain letters")

    for c in username:
        if not c.isalpha() and not c.isdigit() and c != "_":
            return apology(message="Please enter a valid username.")
    if len(username) < 1:
        return apology(message="please enter a username with more than 1 character.")
    hash_pw = generate_password_hash(password)
    time = get_time()
    try:
        if email:
            q = db.execute("SELECT email FROM users WHERE email = :email", {"email": email}).fetchone()
            if q:
                return apology(message="this email already exists")
            db.execute("INSERT INTO users(username, hash, email, time) VALUES(:username, :hash_pw, :email, :time)", {"username": username, "hash_pw": hash_pw, "email": email, "time": time})
            db.commit()
            message="Congratulations!\n You're now registered on AAA Books!"
            #send_email(email, username, message)
        else:
            db.execute("INSERT INTO users(username, hash, time) VALUES(:username, :hash_pw, :time)", {"username": username, "hash_pw": hash_pw, "time": time})
            db.commit()
    except:
        return apology(message="something went wrong with the database.")
    rows = db.execute("SELECT id, username, email FROM users WHERE username = :username", {"username": username}).fetchone()
    session["user_id"] = rows["id"]
    session["username"] = rows["username"]
    session["email"] = rows["email"]
    flash("You're now registered!")
    return redirect("/")

def errorhandler(e):
    """Handle error"""
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return apology(e.name, e.code)


# Listen for errors
for code in default_exceptions:
    app.errorhandler(code)(errorhandler)
