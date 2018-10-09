import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def predict_reference_comp(mnb, vectorizer, word_list):
    # To test what individual things predict,
    # it can deal with a list input or not
    # The maximum probability found is the probability
    # of the predicted classification

    vec_list = vectorizer.transform(word_list).toarray()
    predict_component = mnb.predict(vec_list)
    predict_component_probas = mnb.predict_proba(vec_list)
    predict_component_proba = [
        single_predict.max() for single_predict in predict_component_probas
    ]

    return predict_component, predict_component_proba


def predict_references(mnb, vectorizer, reference_components):

    print(
        "Predicting the categories of ",
        str(len(reference_components)),
        " reference components ... "
    )

    predict_all = []

    # The model cant deal with predicting so many all at once,
    # so predict in a loop
    for component in reference_components['Reference component']:

        # The training data was all from 2017, so the categorisation for
        # year will never work (unless it's 2017)
        # So set to pubyear uf any 4 digit numbers where first
        # 2 digits are 18, 19 or 20 sentences as years.
        valid_years_range = range(1800, 2020)
        if (
           (component.isdigit()
            and int(component) in valid_years_range)
           or (
               len(component) == 6
               and component[1:5].isdigit()
               and int(component[1:5]) in valid_years_range)
           ):
            predict_all.append({
                'Predicted Category': 'PubYear',
                'Prediction Probability': 1
                })

        else:
            # If it's not a year, then classify with the model
            predict_comp, predict_component_proba = predict_reference_comp(
                mnb,
                vectorizer,
                [component]
            )
            predict_all.append({
                'Predicted Category': predict_comp[0],
                'Prediction Probability': predict_component_proba[0]
                })

    predict_all = pd.DataFrame.from_dict(predict_all)

    reference_components_predictions = reference_components.reset_index()

    reference_components_predictions[
        "Predicted Category"
    ] = predict_all['Predicted Category']

    reference_components_predictions[
        "Prediction Probability"
    ] = predict_all['Prediction Probability']

    print("Predictions complete")
    return reference_components_predictions


def decide_components(single_reference):
    """With the predicted components of one reference, decide which of these
    should be used for each component i.e. if there are multiple authors
    predicted and they arent next to each other, then decide which one to use.
    """

    # Add a block number, this groups neighbouring predictions of
    # the same type together.
    block_number = pd.DataFrame({
        'Block': (
            single_reference[
                "Predicted Category"
            ].shift(1) != single_reference[
                "Predicted Category"
            ]).astype(int).cumsum()
    })
    single_reference = pd.concat([single_reference, block_number], axis=1)

    single_reference_components = {}

    for classes in set(single_reference["Predicted Category"]):

        # Are there any sentences of this type (i.e. Authors, Title)?
        classornot = sum(single_reference['Predicted Category'] == classes)

        if classornot != 0:

            # Find how many blocks there are of this class type
            number_blocks = len(
                single_reference['Block'][single_reference[
                    'Predicted Category'
                ] == classes].unique()
            )

            if number_blocks == 1:
                # Just record this block separated by commas
                single_reference_components.update({
                    classes: ", ".join(
                        single_reference[
                            'Reference component'
                        ][single_reference['Predicted Category'] == classes]
                    )
                })
            else:
                # Pick the block containing the highest probability
                # argmax takes the first argument anyway (so if there are 2 of
                # the same probabilities it takes the first one)
                # could decide to do this randomly with argmax.choice()
                # (random choice)

                highest_probability_index = single_reference[
                    single_reference['Predicted Category'] == classes
                ]['Prediction Probability'].idxmax()

                highest_probability_block = single_reference[
                    'Block'
                ][highest_probability_index]

                # Use everything in this block, separated by comma
                single_reference_components.update({
                    classes: ", ".join(
                        single_reference[
                            "Reference component"
                        ][single_reference[
                            'Block'
                        ] == highest_probability_block]
                    )
                })
        else:
            # There are none of this classification, append with blank
            single_reference_components.update({classes: ""})

    return single_reference_components


def single_reference_structure(components_single_reference,
                               prediction_probability_threshold):
    """Predict the structure for a single reference given all the components
    predicted for it.
    """
    # Delete all the rows which have a probability <0.75
    components_single_reference = components_single_reference[
        components_single_reference[
            'Prediction Probability'
        ].astype(float) > prediction_probability_threshold
    ]

    # Decide the best options for each reference component, resulting
    # in one structured reference
    single_reference = decide_components(components_single_reference)
    if single_reference:
        single_reference = pd.DataFrame(
            decide_components(components_single_reference),
            index=[0]
        )

    return single_reference


def predict_structure(reference_components_predictions,
                      prediction_probability_threshold):
    """Predict the structured references for all the references. Go through
    each reference for each document in turn.
    """

    all_structured_references = {}
    document_ids = set(reference_components_predictions['Document id'])

    print(
        "Predicting structure of references from ",
        str(len(document_ids)),
        " documents ... "
    )

    tt = 0
    for document_id in document_ids:
        document = reference_components_predictions.loc[
            reference_components_predictions['Document id'] == document_id
        ]

        reference_ids = set(document['Reference id'])
        for reference_id in reference_ids:
            # The components and predictions for one document one reference:
            components_single_reference = document.loc[
                document['Reference id'] == reference_id
            ].reset_index()

            # Structure:
            single_reference = single_reference_structure(
                components_single_reference,
                prediction_probability_threshold
            )

            if len(single_reference) != 0:

                # Only if there were some enteries for this reference with
                # high prediction probabilies
                single_reference[
                    "Document id"
                ] = components_single_reference['Document id'][0]

                single_reference[
                    "Reference id"
                ] = components_single_reference['Reference id'][0]

                single_reference[
                    "Document uri"
                ] = components_single_reference['Document uri'][0]

                # Merge with the rest:
                if tt == 0:
                    all_structured_references = single_reference
                else:
                    all_structured_references = pd.concat(
                        [all_structured_references, single_reference],
                        axis=0,
                        ignore_index=True,
                        sort=False
                    )
                tt = tt + 1

    print("Reference structure predicted")
    return all_structured_references


def test_structure(predicted_reference_structures,
                   actual_reference_structures):
    """How much does each reference component prediction match the sample given
    (actual_reference_structures)? Don't want to rely on document and reference
    number for matching since these could change based on earlier parts of
    the algorithm.
    """
    # Go through predicted_reference_structures and find the relevant row in
    # actual_reference_structures
    # If it exists then check how much each of the reference components match
    print("Reference structures being tested ... ")
    category_types = [
        'Title',
        'Authors',
        'Journal',
        'PubYear',
        'Volume',
        'Issue',
        'Pagination',
    ]
    similarity_score = pd.DataFrame(columns=category_types)
    # Will get updated if it exists
    mean_similarity_score = None
    if not predicted_reference_structures.empty:
        # Go through all the predicted references and compare the ones which
        # ones were in the sample of actual references
        for index, reference in predicted_reference_structures.iterrows():
            # Which actual references are from the document this predicted
            # reference is from?
            actual_document_references = actual_reference_structures.loc[
                actual_reference_structures[
                    'Document uri'
                ] == reference['Document uri']
            ]
            # For the vast majority there will be none
            if (len(actual_document_references) != 0):
                ####
                # 1. Which one actual reference matches highly with the
                #    predicted reference title?
                ####
                vectorizer = TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 1)
                )
                # Which categories were there for this predicted reference?
                category_exist = []
                for cat in category_types:
                    if cat in predicted_reference_structures.columns:
                        if str(reference[cat]) != 'nan':
                            category_exist.append(cat)
                # Only try to find the similarity matches if there was a
                # title predicted:
                if 'Title' in category_exist:
                    # Vectorize any of the categories that were predicted:
                    if len(category_exist) >= 1:
                        tfidf_matrix = vectorizer.fit_transform(
                            [str(reference[cat]) for cat in category_exist])
                    else:
                        # It didn't predict any of the categories, but this
                        # shouldn't happen I don't think
                        print("error no categories predicted")
                    # Vectorise the titles from the actual references
                    actual_vectors = vectorizer.transform(
                       actual_document_references['Title']
                    )
                    # Find the one actual reference that has the highest
                    # title match
                    # (Index will be 0 for the title one because it was first
                    # in the list in category_types)
                    title_similarity_score = [
                        cosine_similarity(a, tfidf_matrix[0])
                        for a in actual_vectors
                    ]
                    title_similarity_score_max = np.argmax(
                        title_similarity_score
                    )
                    # Get the one actual reference which is most like the
                    # predicted title
                    actual_reference_max = actual_document_references.iloc[
                        title_similarity_score_max
                    ]
                    ####
                    # 2. Vectorise all the categories in this one actual
                    #    reference
                    ####
                    actual_reference_max_vectors = vectorizer.transform([
                        str(actual_reference_max[cat])
                        for cat in category_exist
                    ])
                    # For each of the reference categories that existed for
                    # this predicted reference how well does it match?
                    similarity_score_max = [
                        cosine_similarity(
                            tfidf_matrix[cat],
                            actual_reference_max_vectors[cat]
                        )
                        for cat in range(0, len(category_exist))
                    ]
                    similarity_score_max = [
                        item2
                        for sublist in similarity_score_max
                        for sublist2 in sublist
                        for item2 in sublist2
                    ]
                    # Save how well it did for each category:
                    similarity_score_max = pd.DataFrame(
                        [similarity_score_max],
                        columns=category_exist
                    )
                    similarity_score = similarity_score.append(
                        similarity_score_max
                    )
                    mean_similarity_score = similarity_score.mean()
    print(
        "Average similarity scores between predicted and actual references",
        " for each component, using a sample of ",
        str(len(actual_reference_structures)),
        " references: \n" + str(mean_similarity_score)
    )
    return similarity_score, mean_similarity_score
